from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from Activity.models import (
    ActivityCampaign, FourPlayerResult, FourPlayerTable, LandlordRoundResult, LandlordTable,
    NumberDuel, NumberDuelReward, SpaceActivity,
)
from Space.models import Space
from User.models import User
from utils.auth import get_login_token


class GameLeaderboardTests(TestCase):
    previous_year = datetime(2025, 12, 31, 15, 59, tzinfo=timezone.utc)
    current_year = datetime(2025, 12, 31, 16, tzinfo=timezone.utc)

    def setUp(self):
        self.space = Space.objects.create(name='Rankings', slug='game-rankings', email='rankings@example.test')
        other = Space.objects.create(name='Other space', slug='other-game-rankings', email='other@example.test')
        self.players = [User.create(self.space, name, verified=True) for name in ('A', 'B')]
        self.outsider = User.create(other, 'Other member', verified=True)
        now = patch('Activity.game_leaderboard.timezone.now', return_value=datetime(2026, 6, 1, tzinfo=timezone.utc))
        now.start()
        self.addCleanup(now.stop)

    def run_for(self, key, space=None):
        return SpaceActivity.objects.create(space=space or self.space, campaign=ActivityCampaign.objects.get(key=key),
                                            claimed_at=datetime(2025, 10, 1, tzinfo=timezone.utc))

    def leaderboard(self, key, player=None, status=200, **params):
        player = player or self.players[0]
        reply = self.client.get(f'/activities/{key}/leaderboard', params,
                                HTTP_AUTHORIZATION='Bearer ' + get_login_token(player)['auth'])
        self.assertEqual(reply.status_code, status, reply.content)
        if status == 200:
            self.assertIn('no-store', reply['Cache-Control'])
        return reply.json()['body']

    def test_table_games_rank_points_and_win_rate_independently_in_each_space_and_year(self):
        for key, prefix in (('landlord','K'), ('shuangkou','S'), ('mahjong','M')):
            with self.subTest(key=key):
                run = self.run_for(key)
                external = self.run_for(key, self.outsider.space)
                table_model, result_model = ((LandlordTable, LandlordRoundResult) if key == 'landlord'
                                             else (FourPlayerTable, FourPlayerResult))
                options = {'mode': 'standard' if key == 'mahjong' else 'classic'}
                table = table_model.objects.create(run=run, code=prefix + '2234567', **options)
                other = table_model.objects.create(run=external, code=prefix + '3234567', **options)
                if result_model is LandlordRoundResult:
                    options = {}
                for round_no, points in enumerate((1, 1, -12), 1):
                    for player, score in zip(self.players, (points, -points)):
                        result_model.objects.create(table=table, user=player, round_number=round_no, score=score,
                                                    won=score > 0, finished_at=self.current_year, **options)
                result_model.objects.create(table=table, user=self.players[0], round_number=4, score=80, won=True,
                                            finished_at=self.previous_year, **options)
                result_model.objects.create(table=other, user=self.outsider, round_number=1, score=100, won=True,
                                            finished_at=self.current_year, **options)

                points = self.leaderboard(key, metric='points')
                self.assertEqual((points['year'], points['years']), (2026, [2026, 2025]))
                self.assertEqual([row['points'] for row in points['rows']], [10, -10])
                self.assertEqual(points['mine']['rank'], 2)
                win_rate = self.leaderboard(key, metric='win_rate')
                self.assertEqual(win_rate['rows'][0]['member']['user_id'], self.players[0].id)
                self.assertEqual(win_rate['mine']['win_rate'], 2 / 3)
                self.assertEqual(self.leaderboard(key, year=2025)['mine']['points'], 80)
                self.assertEqual(self.leaderboard(key, player=self.outsider)['rows'][0]['points'], 100)
                self.leaderboard(key, year=2024, status=400)
                self.leaderboard(key, metric='wins', status=400)

    def test_code_duel_uses_finished_year_and_scored_matches_even_after_activity_ends(self):
        key = 'number-duel-2026'
        ActivityCampaign.objects.create(key=key, title='Code Duel', config={'mode': 'number_duel'})
        run = self.run_for(key)
        run.ends_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
        run.save(update_fields=['ends_at'])
        a, b = self.players
        for index, (finish, winner, amount, counted) in enumerate((
            (self.previous_year, a, 20, True),
            (self.current_year, b, -10, True),
            (self.current_year, None, 0, True),
            (self.current_year, a, 0, False),
        ), 1):
            duel = NumberDuel.objects.create(run=run, host=a, guest=b, code=f'CODE{index}XYZ',
                                              winner=winner, funded=counted, finished_at=finish,
                                              deadline=finish, status='finished')
            for player, opponent, points in ((a, b, amount), (b, a, -amount)):
                NumberDuelReward.objects.create(duel=duel, user=player, opponent=opponent, points=points,
                                                counted=counted, date=self.previous_year.date())
        points = self.leaderboard(key)
        self.assertEqual([row['points'] for row in points['rows']], [10, -10])
        rates = self.leaderboard(key, metric='win_rate')
        self.assertEqual((rates['rows'][0]['games'], rates['rows'][0]['wins'], rates['rows'][0]['win_rate']), (2, 1, .5))
        self.assertEqual(rates['mine']['wins'], 0)
        self.assertEqual(self.leaderboard(key, year=2025)['mine']['points'], 20)
        self.leaderboard(key, player=self.outsider, status=403)
