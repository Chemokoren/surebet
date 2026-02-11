"""
Geolocation Service.

Detects user region from IP address for geo-based pricing.
Uses geoip2 (MaxMind) as primary, free IP API as fallback.
"""

import logging
import os
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# East Africa countries for pricing rules
EAST_AFRICA_COUNTRIES = {'KE', 'TZ', 'UG', 'RW', 'BI', 'ET', 'SS', 'SO'}
EAST_AFRICA_COUNTRY_NAMES = {
    'KE': 'Kenya', 'TZ': 'Tanzania', 'UG': 'Uganda',
    'RW': 'Rwanda', 'BI': 'Burundi', 'ET': 'Ethiopia',
}

# Currency mapping
COUNTRY_CURRENCY = {
    'KE': 'KES', 'TZ': 'TZS', 'UG': 'UGX',
    'RW': 'RWF', 'BI': 'BIF', 'ET': 'ETB',
}


class GeoLocationService:
    """
    IP-based geolocation for region detection and pricing.
    """

    CACHE_TTL = 60 * 60 * 24  # 24 hours

    @classmethod
    def detect_from_ip(cls, ip_address: str) -> dict:
        """
        Detect country and region from an IP address.

        Returns:
            {
                'country_code': str,     # ISO 3166-1 alpha-2 (e.g., 'KE')
                'country_name': str,
                'region': str,           # 'east_africa' or 'global'
                'currency': str,         # 'KES', 'USD', etc.
                'city': str,
            }
        """
        if not ip_address or ip_address in ('127.0.0.1', 'localhost', '::1'):
            return cls._default_result()

        # Check cache first
        cache_key = f'geo:ip:{ip_address}'
        cached = cache.get(cache_key)
        if cached:
            return cached

        # Try geoip2 (MaxMind) first
        result = cls._lookup_maxmind(ip_address)

        # Fallback to free API
        if result is None:
            result = cls._lookup_free_api(ip_address)

        # Final fallback
        if result is None:
            result = cls._default_result()

        # Enrich with region and currency
        country_code = result.get('country_code', '')
        result['region'] = 'east_africa' if country_code in EAST_AFRICA_COUNTRIES else 'global'
        # Use KES for all East Africa countries for consistent pricing
        result['currency'] = 'KES' if country_code in EAST_AFRICA_COUNTRIES else 'USD'

        # Cache
        cache.set(cache_key, result, cls.CACHE_TTL)

        return result

    @classmethod
    def _lookup_maxmind(cls, ip_address: str) -> Optional[dict]:
        """Try geoip2 with MaxMind GeoLite2 database."""
        try:
            import geoip2.database

            db_path = getattr(settings, 'GEOIP_PATH', None)
            if not db_path:
                db_path = os.path.join(settings.BASE_DIR, 'data', 'GeoLite2-City.mmdb')

            if not os.path.exists(db_path):
                logger.debug(f"MaxMind DB not found at {db_path}")
                return None

            reader = geoip2.database.Reader(db_path)
            response = reader.city(ip_address)
            reader.close()

            return {
                'country_code': response.country.iso_code or '',
                'country_name': response.country.name or '',
                'city': response.city.name or '',
            }
        except ImportError:
            logger.debug("geoip2 not installed")
            return None
        except Exception as e:
            logger.debug(f"MaxMind lookup failed: {e}")
            return None

    @classmethod
    def _lookup_free_api(cls, ip_address: str) -> Optional[dict]:
        """Fallback: free IP geolocation API."""
        try:
            # ip-api.com (free for non-commercial, 45 req/min)
            response = requests.get(
                f'http://ip-api.com/json/{ip_address}',
                params={'fields': 'status,country,countryCode,city'},
                timeout=3,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    return {
                        'country_code': data.get('countryCode', ''),
                        'country_name': data.get('country', ''),
                        'city': data.get('city', ''),
                    }
        except Exception as e:
            logger.debug(f"Free API lookup failed: {e}")

        return None

    @classmethod
    def _default_result(cls) -> dict:
        """Default to global."""
        return {
            'country_code': '',
            'country_name': 'Unknown',
            'region': 'global',
            'currency': 'USD',
            'city': '',
        }

    @classmethod
    def get_client_ip(cls, request) -> str:
        """
        Extract client IP from Django request.
        Handles X-Forwarded-For for proxied requests.
        """
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')

    @classmethod
    def update_user_profile_geo(cls, user, request) -> dict:
        """
        Detect geo from request IP and update user profile.
        """
        from apps.users.models import UserProfile

        ip = cls.get_client_ip(request)
        geo = cls.detect_from_ip(ip)

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if (
            not profile.detected_ip or
            profile.detected_ip != ip
        ):
            profile.detected_ip = ip
            profile.country_code = geo['country_code']
            profile.region = geo['region']
            profile.currency = geo['currency']
            profile.geo_cached_at = __import__('django.utils.timezone', fromlist=['now']).now()
            profile.save(update_fields=[
                'detected_ip', 'country_code', 'region', 'currency', 'geo_cached_at',
            ])
            logger.info(
                f"Updated geo for user {user.pk}: "
                f"{geo['country_name']} ({geo['region']})"
            )

        return geo
