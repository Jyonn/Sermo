# Welcome message templates

`Message.WelcomeMessageTemplate` is the only live source for welcome content,
ordering, sending, customization growth and the Explorer welcome task. Text,
image, video, audio, file and sticker templates use the same permission and
normalization policy. The default Chinese and English text does not count as
customization. Creating or restoring defaults never awards customization growth.
Completed Explorer themes remain completed, as with the other activity tasks.

Migration `Message.0037` copies remaining legacy text only for users without a
configured template list. Active accounts without either representation receive
a default template. It is idempotent, preserves configured media-only lists,
does not grant points and never drops or clears the old column.

The old `User.welcome_message` database column and its migration-referenced
validator remain archived for recovery and migration history. No runtime
welcome behavior reads or writes it. Do not remove historical migrations or
drop the archive without an explicit data-retention decision.

For existing Android, mini-program and CLI readers, `/users/me/welcome-message`
and the self-profile response retain a derived `welcome_message` (first text
template, or empty for media-only lists). This is not a second stored value.
The stable endpoint accepts only the template-list write contract:
`{"messages": [{"type": 0, "content": "Hello"}]}`. Public user listings no longer
carry the legacy welcome text. The web menu uses `welcome_message_count` from
the self-profile and the editor's returned list length.
