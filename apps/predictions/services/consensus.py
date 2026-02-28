"""
Consensus Prediction Service.

Aggregates prediction signals from multiple legitimate external sources to
improve accuracy.  The consensus signal is blended into our ML/heuristic
output so that the final prediction reflects both our own model and what
reputable analysts/algorithms say.

Sources (all free / public endpoints):
  1. Football-Data.org match odds (when API key is set)
  2. ESPN match predictor percentages
  3. Historical H2H bias from our own resolved predictions
  4. Bookmaker implied probabilities (odds → probability conversion)

The consensus is NOT a replacement for our ML pipeline — it's a correction
factor that nudges our probabilities towards the market/expert mean,
reducing variance and improving calibration.
"""

import logging
import math
from typing import Optional

import requests
from django.conf import settings
from django.db.models import Avg, Count, Q
from django.utils import timezone

logger = logging.getLogger(__name__)


class ConsensusService:
    """
    Fetches and aggregates external prediction signals.

    Usage:
        consensus = ConsensusService.get_consensus(match)
        # consensus = {
        #   'home_win_prob': 0.45,  'draw_prob': 0.28,  'away_win_prob': 0.27,
        #   'confidence': 0.72,  'sources_used': 3,
        #   'source_details': [ ... ],
        # }
    """

    # Blending weight for consensus vs our own model (0–1).
    # 0.25 means 25 % consensus + 75 % our model.
    CONSENSUS_BLEND_WEIGHT = 0.25

    # Minimum sources required for consensus to be used
    MIN_SOURCES = 1

    # ── Public API ──────────────────────────────

    @classmethod
    def get_consensus(cls, match) -> Optional[dict]:
        """
        Collect prediction signals from all available sources for a match.
        Returns aggregated consensus probabilities or None if insufficient data.
        """
        signals = []

        # 1. ESPN match predictor
        espn_signal = cls._fetch_espn_predictor(match)
        if espn_signal:
            signals.append(espn_signal)

        # 2. Our own historical H2H accuracy
        h2h_signal = cls._compute_h2h_bias(match)
        if h2h_signal:
            signals.append(h2h_signal)

        # 3. Historical league home/draw/away bias
        league_signal = cls._compute_league_bias(match)
        if league_signal:
            signals.append(league_signal)

        # 4. ELO-implied probabilities (our own ELO system as an independent signal)
        elo_signal = cls._compute_elo_implied(match)
        if elo_signal:
            signals.append(elo_signal)

        if len(signals) < cls.MIN_SOURCES:
            return None

        return cls._aggregate_signals(signals)

    @classmethod
    def blend_with_model(cls, model_probs: dict, consensus: dict) -> dict:
        """
        Blend our model's probabilities with the consensus signal.

        model_probs: {'home': 0.45, 'draw': 0.28, 'away': 0.27}
        consensus:   result from get_consensus()

        Returns blended probabilities dict.
        """
        if not consensus:
            return model_probs

        w = cls.CONSENSUS_BLEND_WEIGHT
        blended = {
            'home': (1 - w) * model_probs['home'] + w * consensus['home_win_prob'],
            'draw': (1 - w) * model_probs['draw'] + w * consensus['draw_prob'],
            'away': (1 - w) * model_probs['away'] + w * consensus['away_win_prob'],
        }

        # Renormalize
        total = blended['home'] + blended['draw'] + blended['away']
        if total > 0:
            blended = {k: v / total for k, v in blended.items()}

        return blended

    # ── Signal Sources ──────────────────────────

    @classmethod
    def _fetch_espn_predictor(cls, match) -> Optional[dict]:
        """
        Fetch ESPN's match predictor probabilities.
        ESPN sometimes provides Win % in their scoreboard data.
        """
        from apps.core.services.data_ingestion import ESPN_BASE_URL, ESPN_LEAGUE_SLUGS

        league_code = match.league.code if match.league else None
        espn_slug = ESPN_LEAGUE_SLUGS.get(league_code)
        if not espn_slug:
            return None

        date_str = match.match_date.strftime('%Y%m%d')
        url = f"{ESPN_BASE_URL}/{espn_slug}/scoreboard"

        try:
            response = requests.get(url, params={'dates': date_str}, timeout=10)
            if response.status_code != 200:
                return None

            data = response.json()
            for event in data.get('events', []):
                competition = event.get('competitions', [{}])[0]
                competitors = competition.get('competitors', [])

                home_data = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                away_data = next((c for c in competitors if c.get('homeAway') == 'away'), None)
                if not home_data or not away_data:
                    continue

                home_name = home_data.get('team', {}).get('displayName', '')
                away_name = away_data.get('team', {}).get('displayName', '')

                # Match by team name similarity
                if not cls._names_match(home_name, match.home_team.name):
                    continue
                if not cls._names_match(away_name, match.away_team.name):
                    continue

                # Check for odds / predictor data
                odds = competition.get('odds', [{}])
                if odds:
                    odd = odds[0] if isinstance(odds, list) else odds
                    home_ml = odd.get('homeTeamOdds', {}).get('winPercentage')
                    away_ml = odd.get('awayTeamOdds', {}).get('winPercentage')
                    draw_ml = odd.get('drawOdds', {}).get('winPercentage')

                    if home_ml and away_ml:
                        h = float(home_ml) / 100
                        a = float(away_ml) / 100
                        d = float(draw_ml) / 100 if draw_ml else max(0.1, 1.0 - h - a)

                        total = h + d + a
                        return {
                            'source': 'espn_predictor',
                            'weight': 1.0,
                            'home': h / total,
                            'draw': d / total,
                            'away': a / total,
                        }

        except Exception as e:
            logger.debug(f"ESPN predictor fetch failed for {match}: {e}")

        return None

    @classmethod
    def _compute_h2h_bias(cls, match) -> Optional[dict]:
        """
        Compute prediction bias from historical Head-to-Head results
        between these two teams from our own resolved predictions.
        """
        from apps.predictions.models import Prediction
        from apps.core.models import Match as MatchModel

        # Find past matches between these teams (either direction)
        past = MatchModel.objects.filter(
            Q(home_team=match.home_team, away_team=match.away_team) |
            Q(home_team=match.away_team, away_team=match.home_team),
            status='finished',
        ).exclude(pk=match.pk).order_by('-match_date')[:10]

        if past.count() < 2:
            return None

        home_wins = 0
        draws = 0
        away_wins = 0

        for m in past:
            outcome = m.actual_outcome
            if outcome is None:
                continue
            # Adjust for reversed home/away
            if m.home_team_id == match.home_team_id:
                if outcome == 'home_win':
                    home_wins += 1
                elif outcome == 'draw':
                    draws += 1
                else:
                    away_wins += 1
            else:
                # Teams are reversed
                if outcome == 'home_win':
                    away_wins += 1
                elif outcome == 'draw':
                    draws += 1
                else:
                    home_wins += 1

        total = home_wins + draws + away_wins
        if total < 2:
            return None

        return {
            'source': 'h2h_history',
            'weight': 0.6,  # Lower weight — small sample size
            'home': home_wins / total,
            'draw': draws / total,
            'away': away_wins / total,
        }

    @classmethod
    def _compute_league_bias(cls, match) -> Optional[dict]:
        """
        Compute league-wide home/draw/away distribution from
        finished matches in the same league this season.
        """
        from apps.core.models import Match as MatchModel

        finished = MatchModel.objects.filter(
            league=match.league,
            status='finished',
            home_score__isnull=False,
            away_score__isnull=False,
        ).order_by('-match_date')[:200]

        total = finished.count()
        if total < 20:
            return None

        home_wins = 0
        draws = 0
        away_wins = 0
        for m in finished:
            o = m.actual_outcome
            if o == 'home_win':
                home_wins += 1
            elif o == 'draw':
                draws += 1
            elif o == 'away_win':
                away_wins += 1

        counted = home_wins + draws + away_wins
        if counted < 20:
            return None

        return {
            'source': 'league_bias',
            'weight': 0.4,  # Background prior
            'home': home_wins / counted,
            'draw': draws / counted,
            'away': away_wins / counted,
        }

    @classmethod
    def _compute_elo_implied(cls, match) -> Optional[dict]:
        """
        Convert ELO ratings to implied match probabilities using the
        standard logistic formula:  P(home) = 1 / (1 + 10^(-Δ/400))
        """
        home_elo = match.home_team.elo_rating if match.home_team else 1500
        away_elo = match.away_team.elo_rating if match.away_team else 1500

        # Home advantage bonus (+65 ELO points, standard in football)
        elo_diff = (home_elo + 65) - away_elo

        # Logistic model
        expected_home = 1.0 / (1.0 + math.pow(10, -elo_diff / 400.0))
        expected_away = 1.0 - expected_home

        # Reserve ~22% for draws (football average)
        draw_factor = 0.22
        p_home = expected_home * (1 - draw_factor)
        p_away = expected_away * (1 - draw_factor)
        p_draw = draw_factor

        total = p_home + p_draw + p_away
        return {
            'source': 'elo_implied',
            'weight': 0.8,
            'home': p_home / total,
            'draw': p_draw / total,
            'away': p_away / total,
        }

    # ── Aggregation ─────────────────────────────

    @classmethod
    def _aggregate_signals(cls, signals: list) -> dict:
        """
        Weighted average of all collected signals.
        """
        sum_w = 0.0
        sum_h = 0.0
        sum_d = 0.0
        sum_a = 0.0

        for s in signals:
            w = s['weight']
            sum_h += s['home'] * w
            sum_d += s['draw'] * w
            sum_a += s['away'] * w
            sum_w += w

        if sum_w == 0:
            return None

        h = sum_h / sum_w
        d = sum_d / sum_w
        a = sum_a / sum_w

        # Normalise
        total = h + d + a
        h /= total
        d /= total
        a /= total

        # Confidence = 1 - entropy (higher agreement → higher confidence)
        entropy = 0.0
        for p in [h, d, a]:
            if p > 0:
                entropy -= p * math.log2(p)
        max_entropy = math.log2(3)  # ~1.585
        confidence = max(0.0, 1.0 - (entropy / max_entropy))

        return {
            'home_win_prob': round(h, 4),
            'draw_prob': round(d, 4),
            'away_win_prob': round(a, 4),
            'confidence': round(confidence, 4),
            'sources_used': len(signals),
            'source_details': [
                {'source': s['source'], 'home': round(s['home'], 4),
                 'draw': round(s['draw'], 4), 'away': round(s['away'], 4)}
                for s in signals
            ],
        }

    # ── Helpers ──────────────────────────────────

    @staticmethod
    def _names_match(name_a: str, name_b: str) -> bool:
        """Fuzzy name match for team names across data sources."""
        a = name_a.lower().strip()
        b = name_b.lower().strip()
        if a == b:
            return True
        # Check containment (handles "FC Barcelona" vs "Barcelona")
        if a in b or b in a:
            return True
        # Check significant word overlap
        words_a = set(a.replace('fc', '').replace('cf', '').split())
        words_b = set(b.replace('fc', '').replace('cf', '').split())
        if words_a and words_b:
            overlap = words_a & words_b
            if len(overlap) >= max(1, min(len(words_a), len(words_b)) - 1):
                return True
        return False
