from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from Activity.models import ActivityCampaign, ActivityService, UserActivityProgress, ActivityEvent, BgmParticipation, NumberDuel
from Activity.participation import activity_participation
from Space.models import Space
from User.models import User, UserResourceInventory
from utils import auth


class ActivityParticipationTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Participants', slug='participants', email='participants@example.test', group_square_enabled=True, admin_phone_verified_at=timezone.now())
        self.campaign = ActivityCampaign.objects.create(key='participants-test', title='Participants', event_key='test.participate')
        self.run = ActivityService.claim_for_space(self.campaign, self.space)
        self.viewer = User.create(self.space, 'Viewer', verified=True)
        self.peer = User.create(self.space, 'Peer', verified=True)

    def result(self):
        return activity_participation([self.run], self.space.pk)[self.run.pk]

    def progress(self, user, points=0):
        return UserActivityProgress.objects.create(campaign=self.campaign, space_activity=self.run, user=user, earned_points=points)

    def test_viewing_only_does_not_count_and_events_are_deduplicated(self):
        self.progress(self.viewer)
        self.assertEqual(self.result(), {'count': 0, 'users': []})
        progress = self.progress(self.peer)
        for index in range(2):
            ActivityEvent.objects.create(campaign=self.campaign, progress=progress, event_key='test.participate', event_reference=str(index), event_date=timezone.localdate())
        with self.assertNumQueries(2):
            result = self.result()
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['users'][0]['user_id'], self.peer.pk)
        self.assertEqual(set(result['users'][0]), {'user_id', 'name', 'avatar_uri'})

    def test_deleted_and_other_space_users_are_excluded_and_previews_are_bounded(self):
        other = Space.objects.create(name='Other', slug='other-participants', email='other-participants@example.test')
        self.progress(User.create(other, 'Foreign', verified=True), 1)
        for index in range(5):
            self.progress(User.create(self.space, f'Member {index}', verified=True), 1)
        self.progress(self.peer, 1)
        User.objects.filter(pk=self.peer.pk).update(is_deleted=True)
        result = self.result()
        self.assertEqual(result['count'], 5)
        self.assertEqual(len(result['users']), 3)
        self.assertEqual(activity_participation([self.run], other.pk), {})

    def test_joined_bgm_and_duel_players_count_without_page_view_progress(self):
        progress = self.progress(self.peer)
        BgmParticipation.objects.create(progress=progress)
        self.assertEqual(self.result()['count'], 1)
        self.campaign.config = {'mode': 'number_duel'}
        self.campaign.save(update_fields=['config'])
        NumberDuel.objects.create(run=self.run, host=self.viewer, guest=self.peer, code='PRT12345', deadline=timezone.now()+timedelta(minutes=1))
        self.assertEqual(self.result()['count'], 2)

    def test_vip_ownership_counts_only_inside_this_space(self):
        self.campaign.config = {'theme': 'permanent-vip'}
        self.campaign.save(update_fields=['config'])
        UserResourceInventory.grant(self.peer, 'identity', 'vip.permanent')
        self.assertEqual(self.result()['count'], 1)

    def test_authenticated_catalog_and_detail_report_the_same_preview(self):
        self.progress(self.peer, 1)
        headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.viewer)['auth']}"}
        catalog = self.client.get('/activities/active', **headers).json()['body']
        item = next(row for row in catalog if row['key'] == self.campaign.key)
        detail = self.client.get(f'/activities/{self.campaign.key}', **headers).json()['body']
        self.assertEqual(item['participation'], detail['participation'])
        self.assertEqual(item['participation']['count'], 1)
        self.assertIsNone(self.client.get('/activities/active').json()['body'])
