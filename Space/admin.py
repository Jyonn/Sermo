from django.contrib import admin

from Space.models import Space


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'slug', 'email', 'email_verified_at', 'group_square_enabled', 'created_at')
    search_fields = ('name', 'slug', 'email')
