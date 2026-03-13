from django.contrib import admin

from Friendship.models import Friendship


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ('id', 'space', 'user_low', 'user_high', 'status', 'is_system_locked', 'updated_at')
    search_fields = ('user_low__name', 'user_high__name', 'space__slug')
