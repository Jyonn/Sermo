import datetime
import json
from unittest.mock import patch
from django.test import TestCase
from Space.models import Space
from User.models import User, BirthdayLetter
from User.birthday import birthday_status, set_birthday, save_letter, next_occasion, BirthdayErrors, ZONE
from utils.auth import get_login_token, get_space_login_token, get_platform_admin_token

NOW = datetime.datetime(2026, 11, 6, 12, tzinfo=ZONE)


class BirthdayTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Garden', slug='garden', email='garden@example.test')
        self.other_space = Space.objects.create(name='Other', slug='other', email='other@example.test')
        self.me = User.create(self.space, 'Me')
        self.peer = User.create(self.space, 'Peer')
        self.other = User.create(self.other_space, 'Other')

    def headers(self, user=None):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(user or self.me)['auth']}

    def post(self, path, data, headers=None):
        return self.client.post(path, json.dumps(data), content_type='application/json', **(headers or self.headers()))

    def birthday(self, user=None, month=11, day=6):
        user = user or self.me
        set_birthday(user, month, day, NOW)
        user.refresh_from_db()
        return user

    def test_first_selection_and_private_month_day_only(self):
        result = set_birthday(self.me, 11, 6, NOW)
        self.assertTrue(result['active'])
        self.assertFalse(result['can_change'])
        self.assertNotIn('year', result)
        self.assertEqual(result['ends_at'], datetime.datetime(2026, 11, 7, tzinfo=ZONE).timestamp())
        self.me.refresh_from_db()
        self.assertNotIn('birthday_month', self.me.tiny_json())

    def test_rolling_limit_refetches_locked_user_not_stale_request(self):
        set_birthday(self.me, 11, 6, NOW)
        with self.assertRaises(type(BirthdayErrors.LOCKED)):
            set_birthday(self.me, 1, 1, NOW + datetime.timedelta(days=364, hours=23))
        set_birthday(self.me, 1, 1, NOW + datetime.timedelta(days=365))
        self.me.refresh_from_db()
        self.assertEqual(self.me.birthday_month, 1)

    def test_invalid_dates_and_booleans_rejected(self):
        for month, day in [(2,30),(4,31),(0,1),(13,1),(1,0),(True,1),('11',6),(11,None)]:
            with self.assertRaises(type(BirthdayErrors.INVALID_DATE)):
                set_birthday(self.me, month, day, NOW)

    def test_leap_birthdays_observed_february_28(self):
        self.birthday(month=2, day=29)
        self.assertTrue(birthday_status(self.me, datetime.datetime(2027,2,28,10,tzinfo=ZONE))['active'])
        self.assertFalse(birthday_status(self.me, datetime.datetime(2028,2,28,10,tzinfo=ZONE))['active'])
        self.assertTrue(birthday_status(self.me, datetime.datetime(2028,2,29,10,tzinfo=ZONE))['active'])

    def test_next_occasion_and_expiry(self):
        self.birthday()
        self.assertEqual(next_occasion(self.me, NOW).year, 2026)
        self.assertEqual(next_occasion(self.me, NOW + datetime.timedelta(days=1)).year, 2027)
        self.assertFalse(birthday_status(self.me, NOW + datetime.timedelta(days=1))['active'])

    def test_two_independent_letters_and_no_body_in_envelopes(self):
        self.birthday()
        for role in ['space','platform']:
            save_letter(self.me, role, 'Sender', 'admin@example.test', dict(birthday_year=2026,title='For you',body='Private wishes'), NOW)
        status = birthday_status(self.me, NOW)
        self.assertEqual(len(status['letters']), 2)
        self.assertTrue(all('body' not in row for row in status['letters']))
        self.assertEqual(birthday_status(self.me, NOW-datetime.timedelta(days=1))['letters'], [])
        self.assertEqual(BirthdayLetter.objects.count(), 2)

    def test_space_admin_cannot_write_for_other_space_and_members_cannot_write(self):
        self.birthday(self.other)
        path=f'/spaces/admin/users/{self.other.id}/birthday-letter'
        token=get_space_login_token(self.space)['auth']
        self.assertEqual(self.client.get(path, HTTP_AUTHORIZATION='Bearer '+token).status_code,403)
        self.assertEqual(self.post(path,{}, {'HTTP_AUTHORIZATION':'Bearer '+token}).status_code,403)
        self.assertNotEqual(self.post(f'/spaces/admin/users/{self.me.id}/birthday-letter',{}).status_code,200)

    @patch('django.utils.timezone.now', return_value=NOW)
    def test_platform_and_space_write_endpoints(self, clock):
        self.birthday()
        data=dict(birthday_year=2026,title='Happy birthday',body='A private letter')
        for path,token in [
            (f'/spaces/admin/users/{self.me.id}/birthday-letter',get_space_login_token(self.space)['auth']),
            (f'/platform-admin/members/{self.me.id}/birthday-letter',get_platform_admin_token('admin@example.test')['auth']),
        ]:
            response=self.post(path,data,{'HTTP_AUTHORIZATION':'Bearer '+token})
            self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(BirthdayLetter.objects.count(),2)

    @patch('django.utils.timezone.now', return_value=NOW)
    def test_only_owner_can_unseal_only_on_occasion(self, clock):
        self.birthday()
        save_letter(self.me,'space','Garden','admin',dict(birthday_year=2026,title='For you',body='Secret'),NOW)
        row=BirthdayLetter.objects.get()
        url=f'/users/me/birthday/letters/{row.id}/open'
        self.assertEqual(self.post(url,{},self.headers(self.peer)).status_code,404)
        with patch('django.utils.timezone.now',return_value=NOW-datetime.timedelta(days=1)):
            self.assertEqual(self.post(url,{}).status_code,404)
        response=self.post(url,{})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['body']['body'],'Secret')
        with patch('django.utils.timezone.now',return_value=NOW+datetime.timedelta(days=1)):
            self.assertEqual(self.post(url,{}).status_code,200)
            archive=self.client.get('/users/me/birthday/letters',**self.headers()).json()['body']
            self.assertEqual(len(archive),1)
            self.assertNotIn('body',archive[0])
            self.assertEqual(self.client.get('/users/me/birthday/letters',**self.headers(self.peer)).json()['body'],[])

    @patch('django.utils.timezone.now', return_value=NOW)
    def test_seen_acknowledgement_and_setting_api(self, clock):
        response=self.post('/users/me/birthday',dict(month=11,day=6))
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(response.json()['body']['auto_open'])
        self.assertFalse(self.post('/users/me/birthday/seen',{}).json()['body']['auto_open'])
        self.assertEqual(self.post('/users/me/birthday',dict(month=1,day=1)).status_code,403)

    def test_stale_occasion_and_invalid_letter_rejected(self):
        self.birthday()
        for data in [dict(birthday_year=2025,title='Hi',body='Wish'),dict(birthday_year=2026,title='',body='Wish')]:
            with self.assertRaises(Exception):
                save_letter(self.me,'space','Garden','admin',data,NOW)
        self.assertFalse(BirthdayLetter.objects.exists())

    def test_edited_letter_is_unread_but_unchanged_save_is_not(self):
        self.birthday()
        data=dict(birthday_year=2026,title='For you',body='First wish')
        save_letter(self.me,'space','Garden','admin',data,NOW)
        row=BirthdayLetter.objects.get();row.opened_at=NOW;row.save(update_fields=['opened_at'])
        save_letter(self.me,'space','Garden','admin',data,NOW)
        row.refresh_from_db();self.assertEqual(row.opened_at,NOW)
        save_letter(self.me,'space','Garden','admin',{**data,'body':'Updated wish'},NOW)
        row.refresh_from_db();self.assertIsNone(row.opened_at)

    def test_server_clock_defines_active_day_and_expiry(self):
        self.birthday()
        utc_before=datetime.datetime(2026,11,5,15,59,tzinfo=datetime.timezone.utc)
        utc_start=datetime.datetime(2026,11,5,16,0,tzinfo=datetime.timezone.utc)
        self.assertFalse(birthday_status(self.me,utc_before)['active'])
        self.assertTrue(birthday_status(self.me,utc_start)['active'])
        result=birthday_status(self.me,NOW)
        self.assertEqual(result['server_now'],NOW.timestamp())
        self.assertGreater(result['ends_at'],result['server_now'])

    @patch('django.utils.timezone.now', return_value=NOW)
    def test_client_cannot_set_other_users_birthday_or_transfer_celebration(self, clock):
        response=self.post('/users/me/birthday',dict(month=11,day=6,user_id=self.peer.id))
        self.assertEqual(response.status_code,200)
        self.peer.refresh_from_db();self.assertIsNone(self.peer.birthday_month)
        self.assertEqual(self.post('/users/me/birthday/share',dict(user_id=self.peer.id)).status_code,404)
        self.assertNotEqual(self.client.get('/users/me/birthday').status_code,200)
