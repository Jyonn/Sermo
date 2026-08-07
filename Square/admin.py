from django.contrib import admin

from Square.models import Statement, StatementComment, StatementMedia


admin.site.register(Statement)
admin.site.register(StatementMedia)
admin.site.register(StatementComment)
