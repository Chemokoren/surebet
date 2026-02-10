from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from apps.users.models import UserProfile

User = get_user_model()

@receiver(post_save, sender=User)
def create_profile_and_grant_bonus(sender, instance, created, **kwargs):
    """
    Create UserProfile when User is created and grant signup bonus.
    """
    if created:
        profile = UserProfile.objects.create(user=instance)
        # Grant signup bonus (3 premium predictions)
        profile.grant_signup_bonus()
