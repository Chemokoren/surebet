"""
Team Alias / Name Normalisation System.

Solves the problem of different data sources using different names for
the same team. ESPN calls them "Atletico Madrid", football-data uses
"Club Atlético de Madrid", PredictZ says "Atl Madrid", etc.

The canonical team is always the Team model. TeamAlias provides lookups.
"""

import uuid
from django.db import models


class TeamAlias(models.Model):
    """
    Alternative names that map to canonical Team records.

    Usage:
        TeamAlias.resolve("Atl Madrid") → Team <Atletico Madrid>
        TeamAlias.resolve("Man Utd")    → Team <Manchester United>
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    team = models.ForeignKey(
        'core.Team',
        on_delete=models.CASCADE,
        related_name='aliases',
    )
    alias = models.CharField(
        max_length=150,
        unique=True,
        help_text="Alternative team name from an external source",
    )
    source = models.CharField(
        max_length=80,
        blank=True,
        default='',
        help_text="Which source uses this name (e.g. 'predictz', 'forebet', 'espn')",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'team_aliases'
        ordering = ['alias']
        verbose_name_plural = 'team aliases'

    def __str__(self):
        return f"{self.alias} → {self.team.name}"

    @classmethod
    def resolve(cls, name: str):
        """
        Try to resolve a team name via alias lookup.
        Returns Team instance or None.
        """
        if not name:
            return None
        normalised = name.strip().lower()
        try:
            alias = cls.objects.select_related('team').get(
                alias__iexact=normalised
            )
            return alias.team
        except cls.DoesNotExist:
            return None

    @classmethod
    def resolve_or_fuzzy(cls, name: str, matches=None):
        """
        Try exact alias first, then fuzzy match against match list.
        Returns Team or None.
        """
        # 1. Exact alias
        team = cls.resolve(name)
        if team:
            return team

        # 2. Case-insensitive lookup ignoring accents
        from django.db.models import Q
        from apps.core.models import Team
        clean = name.strip()
        team = Team.objects.filter(
            Q(name__iexact=clean) |
            Q(short_name__iexact=clean) |
            Q(code__iexact=clean)
        ).first()
        if team:
            # Auto-create alias for future lookups
            cls.objects.get_or_create(
                alias=clean.lower(),
                defaults={'team': team, 'source': 'auto'},
            )
            return team

        return None

    @classmethod
    def bulk_seed(cls, alias_map: dict, source: str = ''):
        """
        Seed aliases from a dict: {"Alias Name": Team instance, ...}
        Skips existing aliases.
        """
        created = 0
        for alias_name, team in alias_map.items():
            _, was_created = cls.objects.get_or_create(
                alias=alias_name.strip().lower(),
                defaults={'team': team, 'source': source},
            )
            if was_created:
                created += 1
        return created


# ─── Common Team Name Aliases (seeded by migration) ──────────────────────────
# This dict maps common alternative names to canonical names.
# Canonical names must already exist in the Team table.
COMMON_ALIASES = {
    # Premier League
    'man utd': 'Manchester United',
    'man united': 'Manchester United',
    'man city': 'Manchester City',
    'manchester c': 'Manchester City',
    'spurs': 'Tottenham Hotspur',
    'tottenham': 'Tottenham Hotspur',
    'wolves': 'Wolverhampton Wanderers',
    'wolverhampton': 'Wolverhampton Wanderers',
    'west ham utd': 'West Ham United',
    'west ham': 'West Ham United',
    'newcastle utd': 'Newcastle United',
    'newcastle': 'Newcastle United',
    'nott\'m forest': 'Nottingham Forest',
    'nottm forest': 'Nottingham Forest',
    'nott forest': 'Nottingham Forest',
    'brighton': 'Brighton & Hove Albion',
    'brighton hove': 'Brighton & Hove Albion',
    'leicester': 'Leicester City',
    'leeds': 'Leeds United',
    'sheffield utd': 'Sheffield United',
    'sheffield': 'Sheffield United',
    'luton': 'Luton Town',
    'ipswich': 'Ipswich Town',

    # La Liga
    'atletico': 'Atletico Madrid',
    'atl madrid': 'Atletico Madrid',
    'atlético de madrid': 'Atletico Madrid',
    'atlético madrid': 'Atletico Madrid',
    'real sociedad': 'Real Sociedad',
    'real betis': 'Real Betis',
    'athletic bilbao': 'Athletic Club',
    'athletic': 'Athletic Club',
    'celta': 'Celta Vigo',
    'celta de vigo': 'Celta Vigo',
    'rayo': 'Rayo Vallecano',
    'deportivo alavés': 'Alaves',
    'alavés': 'Alaves',

    # Serie A
    'inter': 'Inter Milan',
    'internazionale': 'Inter Milan',
    'inter milano': 'Inter Milan',
    'ac milan': 'AC Milan',
    'milan': 'AC Milan',
    'napoli': 'SSC Napoli',
    'ssc napoli': 'SSC Napoli',
    'lazio': 'SS Lazio',
    'roma': 'AS Roma',
    'as roma': 'AS Roma',
    'juve': 'Juventus',
    'fiorentina': 'ACF Fiorentina',
    'atalanta': 'Atalanta BC',
    'hellas verona': 'Verona',
    'genoa cfc': 'Genoa',
    'us lecce': 'Lecce',
    'udinese calcio': 'Udinese',

    # Bundesliga
    'bayern': 'Bayern Munich',
    'fc bayern': 'Bayern Munich',
    'bayern münchen': 'Bayern Munich',
    'bayern munchen': 'Bayern Munich',
    'dortmund': 'Borussia Dortmund',
    'bvb': 'Borussia Dortmund',
    'gladbach': 'Borussia Monchengladbach',
    "m'gladbach": 'Borussia Monchengladbach',
    'monchengladbach': 'Borussia Monchengladbach',
    'leverkusen': 'Bayer Leverkusen',
    'b leverkusen': 'Bayer Leverkusen',
    'rb leipzig': 'RB Leipzig',
    'leipzig': 'RB Leipzig',
    'wolfsburg': 'VfL Wolfsburg',
    'vfl wolfsburg': 'VfL Wolfsburg',
    'stuttgart': 'VfB Stuttgart',
    'vfb stuttgart': 'VfB Stuttgart',
    'frankfurt': 'Eintracht Frankfurt',
    'e frankfurt': 'Eintracht Frankfurt',
    'freiburg': 'SC Freiburg',
    'sc freiburg': 'SC Freiburg',
    'mainz': 'FSV Mainz 05',
    'mainz 05': 'FSV Mainz 05',
    'augsburg': 'FC Augsburg',
    'fc augsburg': 'FC Augsburg',
    'hoffenheim': 'TSG Hoffenheim',
    'tsg hoffenheim': 'TSG Hoffenheim',
    'union berlin': 'Union Berlin',
    '1. fc union berlin': 'Union Berlin',
    'werder': 'Werder Bremen',
    'werder bremen': 'Werder Bremen',
    'bochum': 'VfL Bochum',
    'heidenheim': '1. FC Heidenheim',

    # Ligue 1
    'psg': 'Paris Saint-Germain',
    'paris sg': 'Paris Saint-Germain',
    'paris saint germain': 'Paris Saint-Germain',
    'marseille': 'Olympique Marseille',
    'om': 'Olympique Marseille',
    'olympique de marseille': 'Olympique Marseille',
    'lyon': 'Olympique Lyonnais',
    'ol': 'Olympique Lyonnais',
    'olympique lyonnais': 'Olympique Lyonnais',
    'monaco': 'AS Monaco',
    'as monaco': 'AS Monaco',
    'lille': 'LOSC Lille',
    'losc': 'LOSC Lille',
    'rennes': 'Stade Rennais',
    'stade rennais': 'Stade Rennais',
    'nice': 'OGC Nice',
    'ogc nice': 'OGC Nice',
    'lens': 'RC Lens',
    'rc lens': 'RC Lens',
    'strasbourg': 'RC Strasbourg',
    'rc strasbourg': 'RC Strasbourg',
    'nantes': 'FC Nantes',
    'fc nantes': 'FC Nantes',
    'reims': 'Stade de Reims',
    'stade de reims': 'Stade de Reims',
    'montpellier': 'Montpellier HSC',
    'toulouse': 'Toulouse FC',
    'brest': 'Stade Brestois',
    'stade brest': 'Stade Brestois',
}
