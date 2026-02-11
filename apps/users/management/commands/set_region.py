"""
Management command to test and force region detection for a user.
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.users.geolocation import GeoLocationService

User = get_user_model()


class Command(BaseCommand):
    help = 'Test region detection or manually set region for a user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ip',
            type=str,
            help='IP address to test geolocation',
        )
        parser.add_argument(
            '--user',
            type=str,
            help='Username to set region for',
        )
        parser.add_argument(
            '--force-region',
            type=str,
            choices=['east_africa', 'global'],
            help='Force a specific region for the user',
        )

    def handle(self, *args, **options):
        # Test IP geolocation
        if options['ip']:
            self.test_ip(options['ip'])
        
        # Set user region
        if options['user']:
            self.set_user_region(
                options['user'],
                options.get('force_region')
            )
        
        # If no args, show help
        if not options['ip'] and not options['user']:
            self.stdout.write(
                self.style.WARNING('No arguments provided. Use --help for usage.')
            )

    def test_ip(self, ip):
        """Test geolocation for an IP."""
        self.stdout.write(f'\nTesting geolocation for IP: {ip}\n')
        
        geo = GeoLocationService.detect_from_ip(ip)
        
        self.stdout.write(self.style.SUCCESS('Geolocation Result:'))
        self.stdout.write(f'  Country Code: {geo["country_code"]}')
        self.stdout.write(f'  Country Name: {geo["country_name"]}')
        self.stdout.write(f'  Region: {geo["region"]}')
        self.stdout.write(f'  Currency: {geo["currency"]}')
        self.stdout.write(f'  City: {geo.get("city", "Unknown")}')
        
        if geo['region'] == 'east_africa':
            self.stdout.write(
                self.style.SUCCESS('\n✅ East Africa detected - will show M-Pesa & KES pricing')
            )
        else:
            self.stdout.write(
                self.style.WARNING('\n⚠️  Global region - will show PayPal/Stripe & USD pricing')
            )

    def set_user_region(self, username, force_region=None):
        """Set region for a user."""
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'User "{username}" not found')
            )
            return
        
        from apps.users.models import UserProfile
        
        profile, created = UserProfile.objects.get_or_create(user=user)
        
        if force_region:
            # Force a specific region
            profile.region = force_region
            profile.currency = 'KES' if force_region == 'east_africa' else 'USD'
            profile.country_code = 'UG' if force_region == 'east_africa' else ''
            profile.save(update_fields=['region', 'currency', 'country_code'])
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✅ Forced {user.username} to region: {force_region}'
                )
            )
            self.stdout.write(f'  Currency: {profile.currency}')
            self.stdout.write(f'  Country: {profile.country_code or "Unknown"}')
        else:
            # Show current setting
            self.stdout.write(f'\nCurrent settings for {user.username}:')
            self.stdout.write(f'  Region: {profile.region}')
            self.stdout.write(f'  Currency: {profile.currency}')
            self.stdout.write(f'  Country: {profile.country_code or "Unknown"}')
