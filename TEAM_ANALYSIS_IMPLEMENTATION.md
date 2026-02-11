# Team Analysis Implementation - Premium & Free-Limited Access

## Overview

The Team Analysis feature at `/analytics/teams/` provides detailed performance metrics for football teams with **premium and free-tier access control**. Users with active subscriptions see advanced analytics, while free-tier users see basic stats and can unlock premium features using credits.

## Architecture

### 1. **TeamAnalyticsService** (`apps/analytics/services/team_analytics_service.py`)

Core service providing all team analytics calculations:

#### Free-Tier Metrics (Always Available)
- **Season Statistics**
  - Win/Draw/Loss counts and rates
  - Goals scored and conceded
  - Clean sheets count and rate
  - Goals per match
  - Goal difference
  
- **Recent Form**
  - Last 5 matches with results (W/D/L)
  - Opponent information
  - Venue (home/away)
  - Score line

#### Premium-Tier Metrics (Subscription/Credit Required)
- **Head-to-Head Records**
  - Historical records against recent opponents
  - Win/draw/loss splits per opponent
  
- **Home vs Away Split**
  - Separate statistics for home and away matches
  - Win rates comparison
  
- **Advanced Form Analysis**
  - Detailed form trends
  - Recent 3/5 match win streaks
  
- **Prediction Accuracy**
  - FuturaPredict accuracy for matches involving this team
  - Split by free vs premium tier predictions
  - Historical accuracy tracking

### Methods

```python
# Get complete team overview with optional premium metrics
TeamAnalyticsService.get_team_overview(team, include_premium=False)

# Get season statistics (free-tier)
TeamAnalyticsService.get_season_stats(team, days=365)

# Get recent form (free-tier)
TeamAnalyticsService.get_recent_form(team, num_matches=5)

# Get premium metrics (subscription-gated)
TeamAnalyticsService.get_premium_metrics(team)

# Search teams by name
TeamAnalyticsService.search_teams(query, league=None)
```

### 2. **TeamAnalysisView** (`apps/api/views.py`)

Django view handling the `/analytics/teams/` page:

```python
class TeamAnalysisView(LoginRequiredMixin, TemplateView):
    """Team analysis page with premium/free access control"""
```

**Features:**
- Requires login (free-tier users still require account)
- Determines access level based on:
  - Active subscription → Premium access
  - Available credits → Can unlock features
  - No subscription/credits → Free tier only
- Passes team analysis data to template
- Includes access level info and feature list

**Context Variables:**
- `team_analysis` - Complete analysis data
- `can_access_premium` - Boolean access flag
- `access_reason` - Human-readable access level description
- `user_credits` - Remaining prediction credits
- `active_sub` - Active subscription object (if any)
- `premium_features` - List of premium feature names
- `teams` - All available teams for search

### 3. **Team Analysis API Endpoint** (`apps/api/v1/views/analytics.py`)

RESTful API for team analysis with access control:

```python
class TeamAnalysisViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
```

#### Endpoints

**GET `/api/v1/teams/<id>/analysis/`**
- Get comprehensive team analysis
- Returns different data based on access level
- Response includes `can_access_premium` flag

**POST `/api/v1/teams/<id>/unlock-premium/`**
- Consume 1 credit to unlock premium features
- Only available for free-tier users with credits
- Returns remaining credits

**GET `/api/v1/teams/search/?q=<query>`**
- Search teams by name
- Optional `league_id` filter

**GET `/api/v1/teams/<id>/team-stats/`**
- Get basic team statistics (free endpoint)
- Optional `days` parameter

### 4. **Frontend Template** (`templates/pages/team_analysis.html`)

Interactive interface for team analysis:

**Free-Tier Features (Always Visible):**
- Team search/autocomplete
- Season statistics cards (win rate, clean sheets, goals)
- Recent form badges
- Goals scored vs conceded chart
- Match results distribution pie chart
- Access level badge

**Premium Features (Conditionally Visible):**
- Home vs Away split statistics
- Head-to-Head records
- FuturaPredict accuracy metrics

**Access Control UI:**
- Lock icon for premium sections when not accessible
- "Unlock" button with credit cost (if credits available)
- "Upgrade to Premium" button (if no credits)
- Alert explaining premium features

**Interaction:**
- Search teams with autocomplete
- Fetch analysis via API
- Display charts using Chart.js
- Smooth animations and transitions

## Access Control Flow

### User Types & Access

```
┌─────────────────────────────────────┐
│        User Type                    │ Access Level
├─────────────────────────────────────┤
│ Anonymous                           │ Redirected to login
│ Free-tier (no subscription)         │ Free stats + can unlock
│ Free-tier with credits              │ Free stats + unlock button
│ Premium subscriber (active)         │ All premium features included
└─────────────────────────────────────┘
```

### Credit Consumption

```python
# When user clicks "Unlock Premium" button
1. POST to /api/v1/teams/<id>/unlock-premium/
2. Backend checks:
   - User has credits? → Consume 1 credit → Success
   - User has no credits? → Error
   - User has subscription? → Error (already has access)
3. Page refreshes to show premium content
```

### Subscription Check

```python
# In view/API, check for active subscription
active_sub = SubscriptionService.get_active_subscription(user)
if active_sub:
    can_access_premium = True
```

## Data Response Format

### API Response (GET `/api/v1/teams/<id>/analysis/`)

```json
{
  "success": true,
  "can_access_premium": true,
  "access_reason": "Premium access via Pro Monthly",
  "user_credits": 5,
  "active_subscription": {
    "plan": "Pro Monthly",
    "expires": "2026-03-11T00:00:00Z"
  },
  "data": {
    "team": {
      "id": "uuid",
      "name": "Manchester United",
      "league": {
        "id": "uuid",
        "name": "Premier League",
        "code": "PL"
      },
      "elo_rating": 1650.5
    },
    "season_stats": {
      "total_matches": 25,
      "wins": 18,
      "draws": 4,
      "losses": 3,
      "win_rate": 72.0,
      "goals_for": 58,
      "goals_against": 21,
      "goal_difference": 37,
      "clean_sheets": 12,
      "clean_sheet_rate": 48.0
    },
    "form": [
      {
        "match_date": "2026-02-08T15:00:00Z",
        "opponent": {"id": "uuid", "name": "Chelsea"},
        "result": "W",
        "score_for": 2,
        "score_against": 1,
        "venue": "home"
      }
    ],
    "premium_metrics": {
      "head_to_head": {...},
      "home_away_split": {...},
      "advanced_form": {...},
      "prediction_accuracy": {...}
    }
  }
}
```

## Usage Example

### 1. View Team Analysis (Browser)

```
User visits: /analytics/teams/?team_id=<team_uuid>
↓
TeamAnalysisView processes request
↓
Checks subscription/credits
↓
Returns template with analysis data
↓
Frontend renders with premium sections locked/visible
```

### 2. Unlock Premium with Credit (API)

```python
# Frontend button click
POST /api/v1/teams/<id>/unlock-premium/
{
  "csrftoken": "token"
}

# Response
{
  "success": true,
  "message": "Premium features unlocked",
  "remaining_credits": 4
}

# Frontend reloads page
```

### 3. Search Teams (API)

```python
GET /api/v1/teams/search/?q=Man United

# Response
{
  "success": true,
  "count": 1,
  "results": [
    {
      "id": "uuid",
      "name": "Manchester United",
      "league": {
        "name": "Premier League",
        "code": "PL"
      }
    }
  ]
}
```

## Integration Points

### 1. **SubscriptionService** 
- `get_active_subscription(user)` - Check if user has premium subscription
- Used to determine access level

### 2. **UserProfile**
- `prediction_credits` - User's remaining credits
- `consume_credit()` - Deduct 1 credit for premium unlock

### 3. **PredictionUsage** (Optional logging)
- Can track which teams users view at premium level

## Premium Feature Pricing

Users can access premium team analysis through:

1. **Subscription** (Recommended)
   - Monthly/yearly plans include all premium features
   - Unlimited team analysis access
   - Example: $9.99/month for Pro plan

2. **Credit Purchase**
   - 1 credit = unlock premium for 1 team
   - Credits available in packs:
     - 5 credits: $4.99
     - 10 credits: $8.99
     - 25 credits: $19.99

## Performance Considerations

### Database Queries
- All calculations use efficient Django ORM queries
- Indexed by `status`, `match_date`, `team_id`
- Minimal N+1 problems with `select_related()` usage

### Caching (Optional)
Future optimization: Cache expensive calculations
```python
cache_key = f"team_analysis:{team.id}:{request.user.id}"
cached_data = cache.get(cache_key)
if not cached_data:
    cached_data = TeamAnalyticsService.get_team_overview(team, include_premium)
    cache.set(cache_key, cached_data, 3600)  # 1 hour
```

## Testing

### Test Coverage

```python
# apps/analytics/tests.py
class TeamAnalyticsServiceTests:
    def test_get_season_stats()
    def test_get_recent_form()
    def test_premium_metrics_require_subscription()
    def test_credit_consumption()

# apps/api/tests.py
class TeamAnalysisAPITests:
    def test_get_analysis_free_user()
    def test_get_analysis_premium_user()
    def test_unlock_premium_insufficient_credits()
    def test_unlock_premium_already_subscriber()
    def test_search_teams()
```

## Configuration

### Settings

```python
# settings/base.py
TEAM_ANALYSIS_CONFIG = {
    'STATS_PERIOD_DAYS': 365,  # Default stats period
    'FORM_MATCHES': 5,          # Recent form matches to show
    'H2H_LIMIT': 10,            # Head-to-head records to calculate
    'PREDICTION_ACCURACY_DAYS': 90,
}
```

## Future Enhancements

1. **Comparison Mode**
   - Compare 2-3 teams side-by-side
   - Premium feature

2. **Injury Reports**
   - Team player injury status
   - Premium feature

3. **Tactical Analysis**
   - Formation preferences
   - Play style metrics
   - Premium feature

4. **Prediction Settings**
   - Teams users want to follow
   - Push notifications for predictions
   - Premium subscribers first

5. **Export/Reports**
   - Generate PDF team reports
   - Premium feature
