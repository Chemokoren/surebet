from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, CrontabSchedule

class Command(BaseCommand):
    help = 'Setup scheduled tasks for prediction generation and verification'

    def handle(self, *args, **kwargs):
        # 1. Schedule: 11:00 PM (23:00) Daily
        # Using get_or_create to avoid duplicates
        schedule_11pm, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='23',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='UTC'
        )

        # 2. Schedule: 12:00 AM (00:00) Daily
        schedule_midnight, _ = CrontabSchedule.objects.get_or_create(
            minute='0',
            hour='0',
            day_of_week='*',
            day_of_month='*',
            month_of_year='*',
            timezone='UTC'
        )

        # Create/Update Generation Task
        # Logic: 11 PM UTC -> Generates predictions for Tomorrow
        PeriodicTask.objects.update_or_create(
            name='Generate Daily Predictions (11 PM UTC)',
            defaults={
                'task': 'apps.predictions.tasks.generate_daily_predictions_task',
                'crontab': schedule_11pm,
                'enabled': True,
                'description': 'Generates predictions for the next day matches.'
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled: Generate Daily Predictions (11 PM UTC)'))

        # Create/Update Verification Task
        # Logic: 00 AM UTC (start of day) -> Verifies predictions for "Today" exist.
        PeriodicTask.objects.update_or_create(
            name='Verify Prediction Availability (Midnight UTC)',
            defaults={
                'task': 'apps.predictions.tasks.verify_predictions_availability_task',
                'crontab': schedule_midnight,
                'enabled': True,
                'description': 'Verifies predictions exist for the new day. Triggers emergency generation if missing.'
            }
        )
        self.stdout.write(self.style.SUCCESS('Scheduled: Verify Prediction Availability (Midnight UTC)'))
