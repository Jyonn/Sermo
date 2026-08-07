from django.contrib import admin

from Square.models import Statement, StatementComment, StatementCommentLike, StatementLike, StatementMedia


admin.site.register(Statement)
admin.site.register(StatementMedia)
admin.site.register(StatementComment)
admin.site.register(StatementLike)
admin.site.register(StatementCommentLike)
