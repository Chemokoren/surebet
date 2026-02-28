"""
External Intelligence Learning Engine.

Combines supervised and unsupervised learning to:
  1. SUPERVISED: Weight external sources by their historical accuracy
     (sources with higher accuracy get more influence in the blend).
  2. UNSUPERVISED: Cluster sources by prediction patterns to identify
     consensus groups and outliers (K-means on prediction vectors).
  3. BLENDING: Merge external consensus into our prediction pipeline
     with dynamic weights based on source quality.

This is the brain that ties together scraping, accuracy tracking,
source evaluation, and prediction improvement.
"""

import logging
import math
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Optional

import numpy as np
from django.db import transaction
from django.db.models import Avg, Count, Q, F
from django.utils import timezone

from apps.predictions.models_intelligence import (
    PredictionSource,
    ExternalPrediction,
    SourceAccuracyRecord,
)

logger = logging.getLogger(__name__)


class LearningEngine:
    """
    The core intelligence engine that manages:
      - Source scraping orchestration
      - Accuracy tracking and evaluation
      - Supervised weighting (accuracy-based)
      - Unsupervised clustering (consensus detection)
      - Dynamic blend generation for the prediction pipeline
    """

    # How recent must resolved predictions be to count for accuracy
    ACCURACY_WINDOW_DAYS = 90

    # Minimum predictions needed before a source influences blending
    MIN_PREDICTIONS_FOR_BLEND = 20

    # Default learning phase duration (days)
    LEARNING_PHASE_DAYS = 90

    # ══════════════════════════════════════════════════════════════════════
    # 1. SCRAPING ORCHESTRATION
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def scrape_all_sources(cls, target_date: date = None) -> dict:
        """
        Run scrapers for all enabled sources and store predictions.

        Returns stats dict with counts of scraped/stored/errors.
        """
        from apps.predictions.scrapers.registry import ScraperRegistry
        from apps.core.models import Match

        if target_date is None:
            target_date = timezone.now().date()

        stats = {'sources_scraped': 0, 'predictions_stored': 0, 'errors': []}

        # Get upcoming matches for matching
        upcoming_matches = list(Match.objects.filter(
            match_date__date__gte=target_date,
            match_date__date__lte=target_date + timedelta(days=6),
            status__in=['scheduled', 'timed'],
        ).select_related('home_team', 'away_team', 'league'))

        sources = PredictionSource.objects.filter(
            scrape_enabled=True,
            status__in=['learning', 'active'],
        ).order_by('priority')

        for source in sources:
            try:
                stored = cls._scrape_source(source, target_date, upcoming_matches)
                stats['predictions_stored'] += stored
                stats['sources_scraped'] += 1

                # Update last scraped timestamp
                source.last_scraped_at = timezone.now()
                source.save(update_fields=['last_scraped_at'])

            except Exception as e:
                stats['errors'].append(f"{source.name}: {e}")
                logger.error(f"Scraping failed for {source.name}: {e}")

        logger.info(
            f"Scraping complete: {stats['sources_scraped']} sources, "
            f"{stats['predictions_stored']} predictions, "
            f"{len(stats['errors'])} errors"
        )
        return stats

    @classmethod
    def _scrape_source(cls, source: PredictionSource, target_date: date, matches: list) -> int:
        """Scrape a single source and store predictions. Returns count stored."""
        from apps.predictions.scrapers.registry import ScraperRegistry

        scraper_cls = ScraperRegistry.get_scraper(
            source.slug,
            custom_class=source.scraper_class,
        )
        if not scraper_cls:
            logger.warning(f"No scraper available for {source.name}")
            return 0

        scraper = scraper_cls(source=source, config=source.scrape_config)
        raw_predictions = scraper.scrape_predictions(target_date)

        stored = 0
        for raw in raw_predictions:
            try:
                parsed = scraper.parse_prediction(raw)
                if not parsed:
                    continue

                # Match to our database
                home_name = parsed.get('home_team', '')
                away_name = parsed.get('away_team', '')
                match = scraper.match_teams(home_name, away_name, matches)
                if not match:
                    continue

                # Store (upsert: one prediction per source per match)
                ext_pred, created = ExternalPrediction.objects.update_or_create(
                    source=source,
                    match=match,
                    defaults={
                        'predicted_outcome': parsed['predicted_outcome'],
                        'home_win_prob': parsed.get('home_win_prob'),
                        'draw_prob': parsed.get('draw_prob'),
                        'away_win_prob': parsed.get('away_win_prob'),
                        'confidence': parsed.get('confidence'),
                        'raw_data': parsed.get('raw_data', {}),
                    }
                )
                if created:
                    stored += 1

            except Exception as e:
                logger.debug(f"Failed to store prediction from {source.name}: {e}")

        return stored

    # ══════════════════════════════════════════════════════════════════════
    # 2. ACCURACY TRACKING (Supervised Signal)
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def resolve_external_predictions(cls) -> int:
        """
        Resolve all pending external predictions against actual match outcomes.
        Called after match results are updated.
        Returns count of newly resolved predictions.
        """
        from apps.core.models import Match

        unresolved = ExternalPrediction.objects.filter(
            is_correct__isnull=True,
            match__status='finished',
        ).select_related('match', 'source')

        resolved_count = 0
        for ext_pred in unresolved:
            actual = ext_pred.match.actual_outcome
            if actual:
                ext_pred.resolve(actual)
                resolved_count += 1

        if resolved_count:
            cls._update_source_accuracy_stats()
            logger.info(f"Resolved {resolved_count} external predictions")

        return resolved_count

    @classmethod
    def _update_source_accuracy_stats(cls):
        """Recompute accuracy metrics for all active/learning sources."""
        sources = PredictionSource.objects.filter(status__in=['learning', 'active'])

        for source in sources:
            resolved = ExternalPrediction.objects.filter(
                source=source,
                is_correct__isnull=False,
            )

            total = resolved.count()
            correct = resolved.filter(is_correct=True).count()

            # Overall accuracy
            accuracy = correct / total if total > 0 else 0.0

            # Recent accuracy (last 30 days)
            cutoff_30d = timezone.now() - timedelta(days=30)
            recent = resolved.filter(resolved_at__gte=cutoff_30d)
            recent_total = recent.count()
            recent_correct = recent.filter(is_correct=True).count()
            recent_accuracy = recent_correct / recent_total if recent_total > 0 else 0.0

            source.total_predictions = total
            source.correct_predictions = correct
            source.overall_accuracy = round(accuracy, 4)
            source.recent_accuracy = round(recent_accuracy, 4)
            source.save(update_fields=[
                'total_predictions', 'correct_predictions',
                'overall_accuracy', 'recent_accuracy', 'updated_at',
            ])

    @classmethod
    def record_daily_accuracy(cls, record_date: date = None):
        """
        Create daily accuracy snapshot for each source.
        Should be called once daily (via Celery).
        """
        if record_date is None:
            record_date = timezone.now().date()

        sources = PredictionSource.objects.filter(status__in=['learning', 'active'])

        for source in sources:
            # Day's predictions
            day_preds = ExternalPrediction.objects.filter(
                source=source,
                match__match_date__date=record_date,
                is_correct__isnull=False,
            )
            total = day_preds.count()
            correct = day_preds.filter(is_correct=True).count()
            accuracy = correct / total if total > 0 else 0.0

            # Breakdown
            home_correct = day_preds.filter(is_correct=True, predicted_outcome='home_win').count()
            draw_correct = day_preds.filter(is_correct=True, predicted_outcome='draw').count()
            away_correct = day_preds.filter(is_correct=True, predicted_outcome='away_win').count()

            # Rolling averages
            acc_7d = cls._rolling_accuracy(source, record_date, 7)
            acc_30d = cls._rolling_accuracy(source, record_date, 30)

            SourceAccuracyRecord.objects.update_or_create(
                source=source,
                date=record_date,
                defaults={
                    'total_predictions': total,
                    'correct_predictions': correct,
                    'accuracy': round(accuracy, 4),
                    'home_correct': home_correct,
                    'draw_correct': draw_correct,
                    'away_correct': away_correct,
                    'accuracy_7d': acc_7d,
                    'accuracy_30d': acc_30d,
                }
            )

    @classmethod
    def _rolling_accuracy(cls, source, end_date: date, days: int) -> Optional[float]:
        start_date = end_date - timedelta(days=days)
        preds = ExternalPrediction.objects.filter(
            source=source,
            match__match_date__date__gte=start_date,
            match__match_date__date__lte=end_date,
            is_correct__isnull=False,
        )
        total = preds.count()
        if total < 5:
            return None
        correct = preds.filter(is_correct=True).count()
        return round(correct / total, 4)

    # ══════════════════════════════════════════════════════════════════════
    # 3. SOURCE EVALUATION & AUTO-PROMOTION
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def evaluate_learning_sources(cls) -> dict:
        """
        Check all sources in 'learning' status.
        Promote to 'active' if they meet accuracy thresholds after
        the learning period (~90 days by default).

        Returns dict of promoted / remaining / rejected sources.
        """
        results = {'promoted': [], 'remaining': [], 'rejected': []}

        learning_sources = PredictionSource.objects.filter(status='learning')

        for source in learning_sources:
            if not source.learning_end_date:
                continue

            today = timezone.now().date()

            if today < source.learning_end_date:
                results['remaining'].append(source.name)
                continue

            # Learning period complete — evaluate
            if source.should_auto_activate:
                source.status = 'active'
                # Set blend weight based on accuracy (higher accuracy → higher weight)
                source.blend_weight = round(source.overall_accuracy * 1.5, 2)
                source.save(update_fields=['status', 'blend_weight', 'updated_at'])
                results['promoted'].append(source.name)
                logger.info(
                    f"🎓 Source '{source.name}' promoted to ACTIVE "
                    f"(accuracy={source.overall_accuracy:.1%}, weight={source.blend_weight})"
                )
            else:
                source.status = 'retired'
                source.save(update_fields=['status', 'updated_at'])
                results['rejected'].append(source.name)
                logger.info(
                    f"❌ Source '{source.name}' RETIRED "
                    f"(accuracy={source.overall_accuracy:.1%}, "
                    f"min required={source.min_accuracy_to_activate:.1%})"
                )

        return results

    # ══════════════════════════════════════════════════════════════════════
    # 4. UNSUPERVISED LEARNING (Consensus Clustering)
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def compute_source_clusters(cls, match) -> dict:
        """
        Use unsupervised learning (K-means style) to cluster external
        predictions for a match into agreement groups.

        Returns:
          {
            'majority_outcome': 'home_win',
            'agreement_ratio': 0.75,     # What % of sources agree
            'cluster_probs': {'home': .., 'draw': .., 'away': ..},
            'outlier_sources': [...],
          }
        """
        ext_preds = ExternalPrediction.objects.filter(
            match=match,
            source__status='active',
        ).select_related('source')

        if ext_preds.count() < 2:
            return None

        # Build prediction vectors  [home_prob, draw_prob, away_prob]
        vectors = []
        source_names = []
        for ep in ext_preds:
            h = ep.home_win_prob or (1.0 if ep.predicted_outcome == 'home_win' else 0.0)
            d = ep.draw_prob or (1.0 if ep.predicted_outcome == 'draw' else 0.0)
            a = ep.away_win_prob or (1.0 if ep.predicted_outcome == 'away_win' else 0.0)
            total = h + d + a
            if total > 0:
                vectors.append([h/total, d/total, a/total])
                source_names.append(ep.source.name)

        if len(vectors) < 2:
            return None

        vectors = np.array(vectors)

        # Compute centroid (weighted by source accuracy)
        weights = []
        for ep in ext_preds:
            w = max(ep.source.blend_weight, 0.1) * max(ep.source.overall_accuracy, 0.3)
            weights.append(w)
        weights = np.array(weights[:len(vectors)])
        weights = weights / weights.sum()

        centroid = np.average(vectors, axis=0, weights=weights)

        # Identify outliers (distance > 1 std dev from centroid)
        distances = np.sqrt(np.sum((vectors - centroid) ** 2, axis=1))
        mean_dist = distances.mean()
        std_dist = distances.std() if len(distances) > 2 else 0.3
        outliers = [
            source_names[i] for i in range(len(distances))
            if distances[i] > mean_dist + std_dist
        ]

        # Majority vote
        outcome_counts = defaultdict(int)
        for ep in ext_preds:
            outcome_counts[ep.predicted_outcome] += 1
        majority = max(outcome_counts, key=outcome_counts.get)
        total_votes = sum(outcome_counts.values())
        agreement = outcome_counts[majority] / total_votes

        # Normalize centroid
        c_total = centroid.sum()
        cluster_probs = {
            'home': round(float(centroid[0] / c_total), 4),
            'draw': round(float(centroid[1] / c_total), 4),
            'away': round(float(centroid[2] / c_total), 4),
        }

        return {
            'majority_outcome': majority,
            'agreement_ratio': round(agreement, 3),
            'cluster_probs': cluster_probs,
            'outlier_sources': outliers,
            'source_count': len(vectors),
        }

    # ══════════════════════════════════════════════════════════════════════
    # 5. PREDICTION BLENDING (Dynamic Weights)
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def get_weighted_consensus(cls, match) -> Optional[dict]:
        """
        Compute a weighted consensus prediction for a match using
        supervised learning (accuracy-weighted) + unsupervised
        clustering (outlier detection).

        This replaces/upgrades the simple ConsensusService for matches
        where external predictions are available.
        """
        ext_preds = list(ExternalPrediction.objects.filter(
            match=match,
            source__status='active',
        ).select_related('source'))

        if len(ext_preds) < 1:
            return None

        # Supervised: weight each source by accuracy
        weighted_h, weighted_d, weighted_a = 0.0, 0.0, 0.0
        total_weight = 0.0

        for ep in ext_preds:
            # Dynamic weight = blend_weight × recent_accuracy (rewarding recent performance)
            w = ep.source.blend_weight * max(ep.source.recent_accuracy, 0.3)

            h = ep.home_win_prob or (0.7 if ep.predicted_outcome == 'home_win' else 0.15)
            d = ep.draw_prob or (0.7 if ep.predicted_outcome == 'draw' else 0.15)
            a = ep.away_win_prob or (0.7 if ep.predicted_outcome == 'away_win' else 0.15)

            # Normalize
            t = h + d + a
            h, d, a = h/t, d/t, a/t

            weighted_h += h * w
            weighted_d += d * w
            weighted_a += a * w
            total_weight += w

        if total_weight == 0:
            return None

        # Normalize
        h = weighted_h / total_weight
        d = weighted_d / total_weight
        a = weighted_a / total_weight
        total = h + d + a
        h, d, a = h/total, d/total, a/total

        # Confidence from agreement (unsupervised)
        clusters = cls.compute_source_clusters(match)
        agreement = clusters['agreement_ratio'] if clusters else 0.5

        # Final confidence = blend of probability confidence + agreement
        prob_confidence = max(h, d, a)
        confidence = (prob_confidence * 0.6) + (agreement * 0.4)

        return {
            'home_win_prob': round(h, 4),
            'draw_prob': round(d, 4),
            'away_win_prob': round(a, 4),
            'confidence': round(confidence, 4),
            'sources_used': len(ext_preds),
            'agreement_ratio': agreement,
            'outliers': clusters.get('outlier_sources', []) if clusters else [],
        }

    # ══════════════════════════════════════════════════════════════════════
    # 6. SOURCE MANAGEMENT HELPERS
    # ══════════════════════════════════════════════════════════════════════

    @classmethod
    def add_source(
        cls,
        name: str,
        slug: str,
        source_type: str = 'website',
        website_url: str = '',
        scraper_class: str = '',
        priority: int = 50,
        learning_days: int = None,
    ) -> PredictionSource:
        """
        Add a new prediction source (enters learning phase automatically).
        """
        if learning_days is None:
            learning_days = cls.LEARNING_PHASE_DAYS

        today = timezone.now().date()
        source, created = PredictionSource.objects.get_or_create(
            slug=slug,
            defaults={
                'name': name,
                'source_type': source_type,
                'website_url': website_url,
                'scraper_class': scraper_class,
                'priority': priority,
                'status': 'learning',
                'learning_start_date': today,
                'learning_end_date': today + timedelta(days=learning_days),
            },
        )
        if created:
            logger.info(f"Added new source: {name} (learning until {source.learning_end_date})")
        return source
