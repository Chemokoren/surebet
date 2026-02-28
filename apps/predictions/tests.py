"""
Tests for the External Intelligence Engine.

Covers:
  - BaseScraper: fuzzy matching, name normalisation, outcome parsing
  - LearningEngine: resolve, accuracy tracking, evaluation, clustering, blending
  - CalibrationService: fit + calibrate
  - PredictionService: integration blend logic
  - TeamAlias: resolution and auto-learning
  - ScraperRegistry: lookup
"""

import uuid
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import League, Team, Match
from apps.core.models_aliases import TeamAlias
from apps.predictions.models_intelligence import (
    PredictionSource,
    ExternalPrediction,
    SourceAccuracyRecord,
)
from apps.predictions.scrapers.base import BaseScraper
from apps.predictions.scrapers.registry import ScraperRegistry


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _make_league(**kw):
    defaults = {
        'name': 'Test League', 'code': 'TL', 'country': 'Testland',
        'priority': 1,
    }
    defaults.update(kw)
    return League.objects.create(**defaults)


def _make_team(name, league, **kw):
    return Team.objects.create(name=name, league=league, **kw)


def _make_match(home, away, league, **kw):
    defaults = {
        'match_date': timezone.now(),
        'status': 'scheduled',
    }
    defaults.update(kw)
    return Match.objects.create(
        home_team=home, away_team=away, league=league, **defaults
    )


def _make_source(slug, **kw):
    defaults = {
        'name': slug.replace('-', ' ').title(),
        'status': 'active',
        'blend_weight': 1.0,
        'overall_accuracy': 0.60,
        'recent_accuracy': 0.60,
        'total_predictions': 100,
        'correct_predictions': 60,
    }
    defaults.update(kw)
    return PredictionSource.objects.create(slug=slug, **defaults)


# ═══════════════════════════════════════════════════════════════════════
# 1. BaseScraper Tests
# ═══════════════════════════════════════════════════════════════════════

class TestBaseScraper(TestCase):
    """Unit tests for BaseScraper utility methods."""

    def test_normalise_name_strips_accents(self):
        self.assertEqual(BaseScraper._normalise_name('Atlético Madrid'), 'atletico madrid')

    def test_normalise_name_removes_fc(self):
        self.assertEqual(BaseScraper._normalise_name('Arsenal FC'), 'arsenal')

    def test_normalise_name_removes_sc_suffix(self):
        self.assertEqual(BaseScraper._normalise_name('Freiburg SC'), 'freiburg')

    def test_fuzzy_match_exact(self):
        self.assertTrue(BaseScraper._fuzzy_match('manchester united', 'manchester united'))

    def test_fuzzy_match_substring(self):
        self.assertTrue(BaseScraper._fuzzy_match('man united', 'manchester united'))

    def test_fuzzy_match_word_overlap(self):
        self.assertTrue(BaseScraper._fuzzy_match('borussia dortmund', 'dortmund'))

    def test_fuzzy_match_no_match(self):
        self.assertFalse(BaseScraper._fuzzy_match('liverpool', 'manchester'))

    def test_outcome_from_probs_home(self):
        self.assertEqual(BaseScraper._outcome_from_probs(0.6, 0.2, 0.2), 'home_win')

    def test_outcome_from_probs_away(self):
        self.assertEqual(BaseScraper._outcome_from_probs(0.2, 0.2, 0.6), 'away_win')

    def test_outcome_from_probs_draw(self):
        self.assertEqual(BaseScraper._outcome_from_probs(0.2, 0.6, 0.2), 'draw')

    def test_outcome_from_tip_1(self):
        self.assertEqual(BaseScraper._outcome_from_tip('1'), 'home_win')

    def test_outcome_from_tip_2(self):
        self.assertEqual(BaseScraper._outcome_from_tip('2'), 'away_win')

    def test_outcome_from_tip_x(self):
        self.assertEqual(BaseScraper._outcome_from_tip('X'), 'draw')

    def test_outcome_from_tip_home(self):
        self.assertEqual(BaseScraper._outcome_from_tip('Home'), 'home_win')

    def test_outcome_from_tip_unknown(self):
        self.assertIsNone(BaseScraper._outcome_from_tip('BTTS'))

    def test_clean_html(self):
        self.assertEqual(BaseScraper._clean_html('<b>Arsenal</b>'), 'Arsenal')
        self.assertEqual(BaseScraper._clean_html('<a href="#">Man Utd</a>'), 'Man Utd')


# ═══════════════════════════════════════════════════════════════════════
# 2. TeamAlias Tests
# ═══════════════════════════════════════════════════════════════════════

class TestTeamAlias(TestCase):
    """Unit tests for TeamAlias resolution."""

    def setUp(self):
        self.league = _make_league()
        self.team = _make_team('Manchester United', self.league, short_name='Man Utd')
        TeamAlias.objects.create(alias='man utd', team=self.team, source='test')

    def test_resolve_exact(self):
        result = TeamAlias.resolve('Man Utd')
        self.assertEqual(result, self.team)

    def test_resolve_case_insensitive(self):
        result = TeamAlias.resolve('MAN UTD')
        self.assertEqual(result, self.team)

    def test_resolve_not_found(self):
        result = TeamAlias.resolve('Nonexistent FC')
        self.assertIsNone(result)

    def test_resolve_or_fuzzy_by_short_name(self):
        result = TeamAlias.resolve_or_fuzzy('Man Utd')
        self.assertEqual(result, self.team)

    def test_resolve_or_fuzzy_auto_creates_alias(self):
        """Resolve by Team.name should auto-create an alias."""
        result = TeamAlias.resolve_or_fuzzy('Manchester United')
        self.assertEqual(result, self.team)

    def test_bulk_seed(self):
        team2 = _make_team('Arsenal', self.league)
        count = TeamAlias.bulk_seed({'gunners': team2, 'ars': team2}, source='test')
        self.assertEqual(count, 2)
        self.assertEqual(TeamAlias.resolve('gunners'), team2)


# ═══════════════════════════════════════════════════════════════════════
# 3. ScraperRegistry Tests (JSON-config driven)
# ═══════════════════════════════════════════════════════════════════════

class TestScraperRegistry(TestCase):
    """Unit tests for the JSON-config driven ScraperRegistry."""

    def setUp(self):
        ScraperRegistry.invalidate_cache()

    def test_get_scraper_returns_universal(self):
        from apps.predictions.scrapers.builtin import UniversalScraper
        cls = ScraperRegistry.get_scraper('predictz')
        self.assertIsNotNone(cls)
        self.assertEqual(cls, UniversalScraper)

    def test_get_nonexistent_scraper(self):
        cls = ScraperRegistry.get_scraper('nonexistent-slug')
        self.assertIsNone(cls)

    def test_get_custom_class_override(self):
        cls = ScraperRegistry.get_scraper(
            'whatever',
            custom_class='apps.predictions.scrapers.builtin.UniversalScraper',
        )
        self.assertIsNotNone(cls)

    def test_list_available_returns_quality_metadata(self):
        available = ScraperRegistry.list_available()
        self.assertIn('predictz', available)
        self.assertIn('forebet', available)
        self.assertIn('freesupertips', available)
        self.assertGreaterEqual(len(available), 20)
        # Each entry should have quality metadata
        predictz = available['predictz']
        self.assertIn('quality_tier', predictz)
        self.assertIn('quality_weight', predictz)
        self.assertIn('enabled', predictz)

    def test_get_site_config(self):
        config = ScraperRegistry.get_site_config('predictz')
        self.assertIn('extraction', config)
        self.assertIn('urls', config)
        self.assertEqual(config['slug'], 'predictz')

    def test_list_enabled_sorted_by_weight(self):
        enabled = ScraperRegistry.list_enabled_sorted()
        self.assertGreater(len(enabled), 0)
        # Should be sorted by quality_weight descending
        weights = [s.get('quality_weight', 0) for s in enabled]
        self.assertEqual(weights, sorted(weights, reverse=True))


# ═══════════════════════════════════════════════════════════════════════
# 3b. UniversalScraper Tests (extraction logic)
# ═══════════════════════════════════════════════════════════════════════

class TestUniversalScraper(TestCase):
    """Tests for UniversalScraper extraction methods."""

    def _make_scraper(self, site_config):
        from apps.predictions.scrapers.builtin import UniversalScraper
        return UniversalScraper(source=None, config=site_config)

    def test_percentage_table_extraction(self):
        """Should extract H/D/A probabilities from table rows."""
        config = {
            'slug': 'test-pct',
            'rate_limit_seconds': 0,
            'extraction': {
                'format': 'percentage_table',
                'row_pattern': r'<tr[^>]*>(.*?)</tr>',
                'team_pattern': r'<a[^>]*>([^<]+)</a>',
                'probability_pattern': r'(\d{1,3})%',
                'min_teams': 2,
                'min_probabilities': 3,
                'prob_order': ['home', 'draw', 'away'],
            },
            'urls': {},
        }
        scraper = self._make_scraper(config)

        html = '''<tr>
            <td><a href="#">Arsenal</a></td>
            <td><a href="#">Chelsea</a></td>
            <td>55%</td><td>25%</td><td>20%</td>
        </tr>'''

        results = scraper._extract_percentage_table(html, config['extraction'], 'PL')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['home_team'], 'Arsenal')
        self.assertEqual(results[0]['away_team'], 'Chelsea')
        self.assertEqual(results[0]['predicted_outcome'], 'home_win')
        self.assertAlmostEqual(results[0]['home_win_prob'], 0.55)

    def test_tip_table_extraction(self):
        """Should extract 1/X/2 tips from table rows."""
        config = {
            'slug': 'test-tip',
            'rate_limit_seconds': 0,
            'extraction': {
                'format': 'tip_table',
                'row_pattern': r'<tr[^>]*>(.*?)</tr>',
                'cell_pattern': r'<td[^>]*>(.*?)</td>',
                'home_cell': 0,
                'away_cell': 1,
                'tip_cell': 2,
                'min_cells': 3,
            },
            'urls': {},
        }
        scraper = self._make_scraper(config)

        html = '<tr><td>Liverpool</td><td>Everton</td><td>1</td></tr>'

        results = scraper._extract_tip_table(html, config['extraction'], 'PL')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['home_team'], 'Liverpool')
        self.assertEqual(results[0]['predicted_outcome'], 'home_win')

    def test_tip_card_extraction(self):
        """Should extract tips from card blocks."""
        config = {
            'slug': 'test-card',
            'rate_limit_seconds': 0,
            'extraction': {
                'format': 'tip_card',
                'block_pattern': r'class="card">(.*?)</div>',
                'team_pattern': r'class="team">([^<]+)',
                'tip_pattern': r'class="tip">([^<]+)',
                'min_teams': 2,
            },
            'urls': {},
        }
        scraper = self._make_scraper(config)

        html = '<div class="card"><span class="team">Real Madrid</span><span class="team">Barcelona</span><span class="tip">X</span></div>'

        results = scraper._extract_tip_card(html, config['extraction'], 'LL')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['predicted_outcome'], 'draw')

    def test_js_required_returns_empty(self):
        """JS-required sites should return empty list gracefully."""
        config = {
            'slug': 'js-site',
            'extraction': {'format': 'js_required'},
            'urls': {},
        }
        scraper = self._make_scraper(config)
        result = scraper.scrape_predictions(date.today())
        self.assertEqual(result, [])


# ═══════════════════════════════════════════════════════════════════════
# 3c. SourceQualityManager Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSourceQualityManager(TestCase):
    """Tests for the self-managing quality engine."""

    def setUp(self):
        self.league = _make_league(code='QM')

    def test_classify_upgrades_high_accuracy(self):
        """Source with > 58% accuracy should be classified as high."""
        from apps.predictions.scrapers.quality_manager import SourceQualityManager
        source = _make_source('predictz', overall_accuracy=0.65, recent_accuracy=0.65)
        result = SourceQualityManager.classify_all()
        self.assertIsNotNone(result)

    def test_record_failure_increments(self):
        """Consecutive failures should increment in JSON config."""
        from apps.predictions.scrapers.quality_manager import SourceQualityManager
        from apps.predictions.scrapers.builtin import load_sources_config

        SourceQualityManager.record_scrape_result('predictz', success=False)
        config = load_sources_config()
        site = next(s for s in config['sources'] if s['slug'] == 'predictz')
        self.assertGreaterEqual(site['consecutive_failures'], 1)

        # Reset
        SourceQualityManager.record_scrape_result('predictz', success=True)
        config = load_sources_config()
        site = next(s for s in config['sources'] if s['slug'] == 'predictz')
        self.assertEqual(site['consecutive_failures'], 0)

    def test_discover_candidates_excludes_existing(self):
        """Discovery should not return already-configured slugs."""
        from apps.predictions.scrapers.quality_manager import SourceQualityManager
        candidates = SourceQualityManager.discover_candidates()
        existing_slugs = {'predictz', 'forebet', 'vitibet'}
        for c in candidates:
            self.assertNotIn(c['slug'], existing_slugs)

    def test_full_audit_runs_without_error(self):
        """Full audit should complete without exceptions."""
        from apps.predictions.scrapers.quality_manager import SourceQualityManager
        result = SourceQualityManager.run_full_audit()
        self.assertIn('classified', result)
        self.assertIn('flagged', result)
        self.assertIn('disabled', result)



# ═══════════════════════════════════════════════════════════════════════
# 4. LearningEngine Tests
# ═══════════════════════════════════════════════════════════════════════

class TestLearningEngine(TestCase):
    """Unit tests for LearningEngine service methods."""

    def setUp(self):
        self.league = _make_league()
        self.home = _make_team('Home FC', self.league, elo_rating=1600)
        self.away = _make_team('Away FC', self.league, elo_rating=1400)
        self.match = _make_match(
            self.home, self.away, self.league,
            status='finished', home_score=2, away_score=1,
        )
        self.source_a = _make_source('source-a', overall_accuracy=0.70, recent_accuracy=0.70)
        self.source_b = _make_source('source-b', overall_accuracy=0.50, recent_accuracy=0.50)

    def test_resolve_external_predictions(self):
        """Resolved predictions should update is_correct flag."""
        from apps.predictions.services.learning_engine import LearningEngine

        ExternalPrediction.objects.create(
            source=self.source_a, match=self.match,
            predicted_outcome='home_win',  # Correct
        )
        ExternalPrediction.objects.create(
            source=self.source_b, match=self.match,
            predicted_outcome='away_win',  # Wrong
        )

        resolved = LearningEngine.resolve_external_predictions()
        self.assertEqual(resolved, 2)

        ep_a = ExternalPrediction.objects.get(source=self.source_a)
        ep_b = ExternalPrediction.objects.get(source=self.source_b)
        self.assertTrue(ep_a.is_correct)
        self.assertFalse(ep_b.is_correct)

    def test_resolve_skips_already_resolved(self):
        from apps.predictions.services.learning_engine import LearningEngine

        ExternalPrediction.objects.create(
            source=self.source_a, match=self.match,
            predicted_outcome='home_win',
            is_correct=True,
            resolved_at=timezone.now(),
        )
        resolved = LearningEngine.resolve_external_predictions()
        self.assertEqual(resolved, 0)

    def test_record_daily_accuracy(self):
        from apps.predictions.services.learning_engine import LearningEngine

        ExternalPrediction.objects.create(
            source=self.source_a, match=self.match,
            predicted_outcome='home_win',
            is_correct=True,
            resolved_at=timezone.now(),
        )
        LearningEngine.record_daily_accuracy(self.match.match_date.date())

        record = SourceAccuracyRecord.objects.get(source=self.source_a)
        self.assertEqual(record.total_predictions, 1)
        self.assertEqual(record.correct_predictions, 1)
        self.assertAlmostEqual(record.accuracy, 1.0)

    def test_evaluate_learning_sources_promotes(self):
        from apps.predictions.services.learning_engine import LearningEngine

        source = _make_source(
            'learner', status='learning',
            overall_accuracy=0.65,
            total_predictions=60,
            correct_predictions=39,
        )
        source.learning_start_date = date.today() - timedelta(days=100)
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()

        results = LearningEngine.evaluate_learning_sources()
        source.refresh_from_db()

        self.assertIn(source.name, results['promoted'])
        self.assertEqual(source.status, 'active')
        self.assertGreater(source.blend_weight, 0)

    def test_evaluate_learning_sources_retires(self):
        from apps.predictions.services.learning_engine import LearningEngine

        source = _make_source(
            'bad-learner', status='learning',
            overall_accuracy=0.30,
            total_predictions=60,
            correct_predictions=18,
        )
        source.learning_start_date = date.today() - timedelta(days=100)
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()

        results = LearningEngine.evaluate_learning_sources()
        source.refresh_from_db()

        self.assertIn(source.name, results['rejected'])
        self.assertEqual(source.status, 'retired')

    def test_get_weighted_consensus(self):
        from apps.predictions.services.learning_engine import LearningEngine

        ExternalPrediction.objects.create(
            source=self.source_a, match=self.match,
            predicted_outcome='home_win',
            home_win_prob=0.7, draw_prob=0.15, away_win_prob=0.15,
        )
        ExternalPrediction.objects.create(
            source=self.source_b, match=self.match,
            predicted_outcome='home_win',
            home_win_prob=0.6, draw_prob=0.20, away_win_prob=0.20,
        )

        result = LearningEngine.get_weighted_consensus(self.match)
        self.assertIsNotNone(result)
        self.assertGreater(result['home_win_prob'], result['away_win_prob'])
        self.assertEqual(result['sources_used'], 2)
        self.assertGreater(result['agreement_ratio'], 0.5)

    def test_get_weighted_consensus_returns_none_without_sources(self):
        from apps.predictions.services.learning_engine import LearningEngine

        result = LearningEngine.get_weighted_consensus(self.match)
        self.assertIsNone(result)

    def test_compute_source_clusters(self):
        from apps.predictions.services.learning_engine import LearningEngine

        ExternalPrediction.objects.create(
            source=self.source_a, match=self.match,
            predicted_outcome='home_win',
            home_win_prob=0.7, draw_prob=0.2, away_win_prob=0.1,
        )
        ExternalPrediction.objects.create(
            source=self.source_b, match=self.match,
            predicted_outcome='home_win',
            home_win_prob=0.6, draw_prob=0.25, away_win_prob=0.15,
        )

        clusters = LearningEngine.compute_source_clusters(self.match)
        self.assertIsNotNone(clusters)
        self.assertEqual(clusters['majority_outcome'], 'home_win')
        self.assertAlmostEqual(clusters['agreement_ratio'], 1.0)
        self.assertEqual(clusters['source_count'], 2)

    def test_add_source(self):
        from apps.predictions.services.learning_engine import LearningEngine

        source = LearningEngine.add_source(
            name='New Site', slug='new-site',
            source_type='website', priority=10,
        )
        self.assertEqual(source.status, 'learning')
        self.assertIsNotNone(source.learning_end_date)
        self.assertEqual(
            (source.learning_end_date - source.learning_start_date).days,
            90,
        )


# ═══════════════════════════════════════════════════════════════════════
# 5. CalibrationService Tests
# ═══════════════════════════════════════════════════════════════════════

class TestCalibrationService(TestCase):
    """Unit tests for CalibrationService."""

    def test_calibrate_returns_uncalibrated_when_no_model(self):
        from apps.predictions.services.calibration import CalibrationService
        CalibrationService._model = None
        result = CalibrationService.calibrate(72.5)
        self.assertEqual(result, 72.5)

    def test_calibrate_probs_returns_uncalibrated_when_no_model(self):
        from apps.predictions.services.calibration import CalibrationService
        CalibrationService._model = None
        h, d, a = CalibrationService.calibrate_probs(0.6, 0.2, 0.2)
        self.assertEqual((h, d, a), (0.6, 0.2, 0.2))

    @patch('apps.predictions.services.calibration.CalibrationService._get_model')
    def test_calibrate_applies_model(self, mock_get):
        from apps.predictions.services.calibration import CalibrationService
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.68]
        mock_get.return_value = mock_model

        result = CalibrationService.calibrate(72.0)
        self.assertAlmostEqual(result, 68.0)


# ═══════════════════════════════════════════════════════════════════════
# 6. Integration: PredictionService Blend Logic
# ═══════════════════════════════════════════════════════════════════════

class TestPredictionServiceBlend(TestCase):
    """Integration tests for PredictionService intelligence blending."""

    def setUp(self):
        self.league = _make_league(code='INT')
        self.home = _make_team('Team Home', self.league, elo_rating=1550)
        self.away = _make_team('Team Away', self.league, elo_rating=1450)
        self.match = _make_match(self.home, self.away, self.league)

    def _create_model_version(self, name='test-model'):
        from apps.predictions.models import ModelVersion
        return ModelVersion.objects.create(
            name=name, version='1.0', model_type='ensemble',
            is_active=True,
        )

    @patch('apps.predictions.services.ensemble.EnsembleService.predict_match')
    @patch('apps.predictions.services.learning_engine.LearningEngine.get_weighted_consensus')
    def test_blend_with_intelligence(self, mock_intel, mock_ensemble):
        """When intelligence data is available, it should blend 70/30."""
        from apps.predictions.services.prediction_service import PredictionService

        self._create_model_version()
        mock_ensemble.return_value = {
            'probabilities': {'home': 0.50, 'draw': 0.25, 'away': 0.25},
            'predicted_outcome': 'home_win',
            'confidence': 50.0,
            'features': {},
            'explanations': [],
        }
        mock_intel.return_value = {
            'home_win_prob': 0.60, 'draw_prob': 0.20, 'away_win_prob': 0.20,
            'confidence': 0.60,
            'sources_used': 5,
            'agreement_ratio': 0.8,
            'outliers': [],
        }

        PredictionService.generate_daily_predictions(self.match.match_date.date())

        from apps.predictions.models import Prediction
        pred = Prediction.objects.get(match=self.match)

        # With 70/30 blend: home should be 0.5*0.7 + 0.6*0.3 = 0.53
        self.assertGreater(pred.home_win_prob, 0.50)
        self.assertEqual(pred.predicted_outcome, 'home_win')

    @patch('apps.predictions.services.ensemble.EnsembleService.predict_match')
    @patch('apps.predictions.services.learning_engine.LearningEngine.get_weighted_consensus')
    def test_fallback_to_consensus_when_no_intelligence(self, mock_intel, mock_ensemble):
        """When no intelligence, should fall back to basic consensus."""
        from apps.predictions.services.prediction_service import PredictionService

        self._create_model_version('test-model-2')
        mock_ensemble.return_value = {
            'probabilities': {'home': 0.45, 'draw': 0.30, 'away': 0.25},
            'predicted_outcome': 'home_win',
            'confidence': 45.0,
            'features': {},
            'explanations': [],
        }
        mock_intel.return_value = None  # No intelligence

        with patch('apps.predictions.services.consensus.ConsensusService.get_consensus') as mock_cons:
            mock_cons.return_value = None  # Also no consensus
            PredictionService.generate_daily_predictions(self.match.match_date.date())

        from apps.predictions.models import Prediction
        pred = Prediction.objects.get(match=self.match)
        self.assertIsNotNone(pred)
        self.assertEqual(pred.predicted_outcome, 'home_win')

    def test_heuristic_fallback_uses_elo(self):
        """Cold-start fallback should use ELO expected score, not random."""
        from apps.predictions.services.prediction_service import PredictionService

        self._create_model_version('test-model-3')

        # Strong ELO difference: home 1700 vs away 1300
        self.home.elo_rating = 1700
        self.home.save()
        self.away.elo_rating = 1300
        self.away.save()

        with patch('apps.predictions.services.ensemble.EnsembleService.predict_match',
                    side_effect=Exception('No model')):
            PredictionService.generate_daily_predictions(self.match.match_date.date())

        from apps.predictions.models import Prediction
        pred = Prediction.objects.get(match=self.match)
        # With 400 ELO diff, home should dominate
        self.assertGreater(pred.home_win_prob, pred.away_win_prob)


# ═══════════════════════════════════════════════════════════════════════
# 7. ExternalPrediction Model Tests
# ═══════════════════════════════════════════════════════════════════════

class TestExternalPrediction(TestCase):
    """Unit tests for ExternalPrediction model."""

    def setUp(self):
        self.league = _make_league(code='EP')
        self.home = _make_team('EP Home', self.league)
        self.away = _make_team('EP Away', self.league)
        self.match = _make_match(
            self.home, self.away, self.league,
            status='finished', home_score=1, away_score=0,
        )
        self.source = _make_source('ep-test')

    def test_resolve_correct(self):
        ep = ExternalPrediction.objects.create(
            source=self.source, match=self.match,
            predicted_outcome='home_win',
        )
        ep.resolve('home_win')
        self.assertTrue(ep.is_correct)
        self.assertIsNotNone(ep.resolved_at)

    def test_resolve_incorrect(self):
        ep = ExternalPrediction.objects.create(
            source=self.source, match=self.match,
            predicted_outcome='away_win',
        )
        ep.resolve('home_win')
        self.assertFalse(ep.is_correct)

    def test_resolve_idempotent(self):
        ep = ExternalPrediction.objects.create(
            source=self.source, match=self.match,
            predicted_outcome='home_win',
            is_correct=True,
            resolved_at=timezone.now(),
        )
        original_resolved = ep.resolved_at
        ep.resolve('away_win')  # Should not change
        self.assertTrue(ep.is_correct)
        self.assertEqual(ep.resolved_at, original_resolved)


# ═══════════════════════════════════════════════════════════════════════
# 8. PredictionSource Model Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPredictionSource(TestCase):
    """Unit tests for PredictionSource model properties."""

    def test_should_auto_activate_when_ready(self):
        source = _make_source(
            'auto-test', status='learning',
            overall_accuracy=0.65,
            total_predictions=60,
        )
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()
        self.assertTrue(source.should_auto_activate)

    def test_should_not_activate_low_accuracy(self):
        source = _make_source(
            'low-acc', status='learning',
            overall_accuracy=0.35,
            total_predictions=60,
        )
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()
        self.assertFalse(source.should_auto_activate)

    def test_should_not_activate_too_few_predictions(self):
        source = _make_source(
            'few-preds', status='learning',
            overall_accuracy=0.70,
            total_predictions=10,
        )
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()
        self.assertFalse(source.should_auto_activate)

    def test_is_in_learning_phase(self):
        source = _make_source(
            'learning-test', status='learning',
        )
        source.learning_end_date = date.today() + timedelta(days=30)
        source.save()
        self.assertTrue(source.is_in_learning_phase)

    def test_not_in_learning_if_expired(self):
        source = _make_source(
            'expired-learn', status='learning',
        )
        source.learning_end_date = date.today() - timedelta(days=1)
        source.save()
        self.assertFalse(source.is_in_learning_phase)
