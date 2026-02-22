"""
Management command: create_test_users
======================================
Creates (or resets) test accounts that cover every subscription tier so
you can physically log in and verify access works end-to-end.

Usage
-----
    python manage.py create_test_users           # create / update
    python manage.py create_test_users --reset   # wipe & recreate

Test accounts created
---------------------
  Username            Password              Plan / Tier
  ──────────────────  ────────────────────  ─────────────────────────────────
  test_free           FuturaTest@free1      No subscription  (3 free tips)
  test_5daily         FuturaTest@5daily1    Starter – 5 games / day
  test_10daily        FuturaTest@10daily1   Pro     – 10 games / day
  test_20daily        FuturaTest@20daily1   Business – 20 games / day
  test_monthly        FuturaTest@monthly1   Monthly All-Access – 30 games / day

All subscriptions are set to *active* and expire 30 days from the time
the command is run, so they will be usable immediately.
"""

from __future__ import annotations

import textwrap
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.payments.models import SubscriptionPlan, UserSubscription
from apps.users.models import UserProfile


# ── Test account definitions ──────────────────────────────────────────────────

TEST_ACCOUNTS = [
    {
        "username":    "test_free",
        "password":    "FuturaTest@free1",
        "email":       "test_free@futurapredict.test",
        "first_name":  "Free",
        "last_name":   "Tester",
        "plan_slug":   None,   # no subscription
        "description": "No subscription — sees 3 free predictions only",
    },
    {
        "username":    "test_5daily",
        "password":    "FuturaTest@5daily1",
        "email":       "test_5daily@futurapredict.test",
        "first_name":  "Starter",
        "last_name":   "Tester",
        "plan_slug":   "starter-5-daily",
        "description": "Starter plan — 5 games / day",
    },
    {
        "username":    "test_10daily",
        "password":    "FuturaTest@10daily1",
        "email":       "test_10daily@futurapredict.test",
        "first_name":  "Pro",
        "last_name":   "Tester",
        "plan_slug":   "pro-10-daily",
        "description": "Pro plan — 10 games / day",
    },
    {
        "username":    "test_20daily",
        "password":    "FuturaTest@20daily1",
        "email":       "test_20daily@futurapredict.test",
        "first_name":  "Business",
        "last_name":   "Tester",
        "plan_slug":   "business-20-daily",
        "description": "Business plan — 20 games / day",
    },
    {
        "username":    "test_monthly",
        "password":    "FuturaTest@monthly1",
        "email":       "test_monthly@futurapredict.test",
        "first_name":  "Monthly",
        "last_name":   "Tester",
        "plan_slug":   "monthly-allaccess",
        "description": "Monthly All-Access — 30 games / day",
    },
]

# ── Subscription plans to ensure exist ───────────────────────────────────────

PLANS = [
    {
        "slug":                  "starter-5-daily",
        "name":                  "Starter (5 / day)",
        "interval":              "monthly",
        "price":                 "5.00",
        "currency":              "USD",
        "region":                "global",
        "daily_prediction_limit": 5,
        "features":              ["5 predictions per day", "All 5 top leagues", "Confidence scores"],
        "display_order":         1,
    },
    {
        "slug":                  "pro-10-daily",
        "name":                  "Pro (10 / day)",
        "interval":              "monthly",
        "price":                 "10.00",
        "currency":              "USD",
        "region":                "global",
        "daily_prediction_limit": 10,
        "features":              ["10 predictions per day", "All 5 top leagues", "Confidence scores", "Match analysis"],
        "display_order":         2,
    },
    {
        "slug":                  "business-20-daily",
        "name":                  "Business (20 / day)",
        "interval":              "monthly",
        "price":                 "20.00",
        "currency":              "USD",
        "region":                "global",
        "daily_prediction_limit": 20,
        "features":              ["20 predictions per day", "All 5 top leagues", "Full analysis", "Priority support"],
        "display_order":         3,
    },
    {
        "slug":                  "monthly-allaccess",
        "name":                  "Monthly All-Access",
        "interval":              "monthly",
        "price":                 "30.00",
        "currency":              "USD",
        "region":                "global",
        "daily_prediction_limit": 30,
        "features":              ["30 predictions per day", "All 5 top leagues", "Full analysis", "Priority support", "Early access"],
        "display_order":         4,
    },
]


class Command(BaseCommand):
    help = "Create (or reset) test accounts for every subscription tier."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            default=False,
            help="Delete and fully recreate each test account (clears any usage history).",
        )

    # ── Entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        reset = options["reset"]
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== FuturaPredict – Test User Setup ===\n"))

        # 1. Ensure subscription plans exist
        plan_map = self._ensure_plans()

        # 2. Create / reset users
        results = []
        for acct in TEST_ACCOUNTS:
            user, action = self._ensure_user(acct, reset=reset)
            plan = plan_map.get(acct["plan_slug"]) if acct["plan_slug"] else None
            sub_action = self._ensure_subscription(user, plan, reset=reset)
            results.append((acct, user, action, sub_action))

        # 3. Print credentials table
        self._print_credentials_table(results)

    # ── Plans ─────────────────────────────────────────────────────────────────

    def _ensure_plans(self) -> dict[str, SubscriptionPlan]:
        """Create or update all test subscription plans. Returns slug → plan map."""
        plan_map: dict[str, SubscriptionPlan] = {}
        self.stdout.write(self.style.HTTP_INFO("» Subscription plans"))

        for data in PLANS:
            slug = data["slug"]
            plan, created = SubscriptionPlan.objects.update_or_create(
                slug=slug,
                defaults={
                    "name":                   data["name"],
                    "interval":               data["interval"],
                    "price":                  data["price"],
                    "currency":               data["currency"],
                    "region":                 data["region"],
                    "daily_prediction_limit": data["daily_prediction_limit"],
                    "features":               data["features"],
                    "is_active":              True,
                    "display_order":          data["display_order"],
                },
            )
            label = self.style.SUCCESS("  CREATED") if created else "  updated"
            self.stdout.write(f"{label}  {plan.name}  ({plan.daily_prediction_limit} games/day)")
            plan_map[slug] = plan

        return plan_map

    # ── Users ─────────────────────────────────────────────────────────────────

    def _ensure_user(self, acct: dict, *, reset: bool) -> tuple[User, str]:
        """Return (user, action_label). Creates or resets the user."""
        username = acct["username"]

        if reset:
            User.objects.filter(username=username).delete()

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email":      acct["email"],
                "first_name": acct["first_name"],
                "last_name":  acct["last_name"],
                "is_active":  True,
            },
        )

        # Always (re-)set the password so it matches this file
        user.set_password(acct["password"])
        user.is_active = True
        user.save(update_fields=["password", "is_active"])

        # Ensure profile exists with signup bonus granted
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if not profile.signup_bonus_granted:
            profile.signup_bonus_granted = True
            profile.save(update_fields=["signup_bonus_granted"])

        action = "CREATED" if created else "updated"
        return user, action

    # ── Subscriptions ─────────────────────────────────────────────────────────

    def _ensure_subscription(
        self,
        user: User,
        plan: SubscriptionPlan | None,
        *,
        reset: bool,
    ) -> str:
        """
        Create or refresh an active UserSubscription for this user.
        Returns a short status string.
        """
        # Cancel / remove existing subscriptions first
        UserSubscription.objects.filter(user=user).update(status="cancelled")

        if plan is None:
            return "no subscription"

        now = timezone.now()
        sub = UserSubscription.objects.create(
            user=user,
            plan=plan,
            status="active",
            started_at=now,
            expires_at=now + timedelta(days=30),
            auto_renew=False,
            daily_predictions_used_today=0,
            last_daily_reset=now.date(),
        )
        return f"active → {plan.name} (expires {sub.expires_at.strftime('%Y-%m-%d')})"

    # ── Output ────────────────────────────────────────────────────────────────

    def _print_credentials_table(self, results: list) -> None:
        """Print a clean credentials reference table."""
        SEP  = "─" * 80
        DSEP = "═" * 80

        self.stdout.write(f"\n{DSEP}")
        self.stdout.write(self.style.MIGRATE_HEADING(
            "  TEST CREDENTIALS  –  use these to log in at /account/login/"
        ))
        self.stdout.write(DSEP)
        self.stdout.write(
            f"  {'Username':<20}  {'Password':<24}  {'Plan / Access'}"
        )
        self.stdout.write(SEP)

        for acct, user, action, sub_action in results:
            plan_label = acct["description"]
            self.stdout.write(
                f"  {acct['username']:<20}  {acct['password']:<24}  {plan_label}"
            )

        self.stdout.write(DSEP)
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(
            "  ⚠  These accounts are for local testing only.\n"
            "     Remove them before deploying to production:\n"
            "     python manage.py shell -c \""
            "from django.contrib.auth.models import User; "
            "[u.delete() for u in User.objects.filter(username__startswith='test_')]\""
        ))
        self.stdout.write("")

        # Per-account detailed breakdown
        self.stdout.write(self.style.MIGRATE_HEADING("  Detailed breakdown"))
        self.stdout.write(SEP)
        for acct, user, action, sub_action in results:
            status_colour = self.style.SUCCESS if action == "CREATED" else self.style.HTTP_INFO
            self.stdout.write(
                f"  {status_colour(action.upper()):<10}  "
                f"user: {acct['username']}  |  "
                f"sub: {sub_action}"
            )
        self.stdout.write(f"{SEP}\n")

