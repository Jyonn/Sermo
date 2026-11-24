from math import exp


# Each group is ordered by the reach of its album and representative songs.
# Keep one release per studio album. The two EPs sold for JJ's 14th album count
# as one studio project. Full-length covers and new studio recordings count.
STUDIO_ALBUMS = {
    'jay': (
        ('jay-fantasy', 'jay-common-jasmine', 'jay-chopin', 'jay-dimensions', 'jay-ye', 'jay-capricorn', 'jay-still-fantasy'),
        ('jay-jay', 'jay-on-the-run', 'jay-era', 'jay-opus-12', 'jay-greatest'),
        ('jay-not-bad', 'jay-bedtime', 'jay-son-of-the-sun'),
        ('jay-exclamation',),
    ),
    'leehom': (
        ('leehom-only', 'leehom-shangri-la', 'leehom-heroes', 'leehom-heartbeat', 'leehom-martial-arts',
         'leehom-revolution', 'leehom-unbelievable'),
        ('leehom-change-me', 'leehom-cant-miss', 'leehom-first-day', 'leehom-ai-love', 'leehom-your-love', 'leehom-beethoven'),
        ('leehom-miss-you', 'leehom-hear-my-song', 'leehom-blank-paper'),
        ('leehom-hear-my-voice-jp',),
    ),
    'david': (
        ('david-dt', 'david-black-tangerine', 'david-great-leap'),
        ('david-okay', 'david-beautiful'),
        ('david-stupid-pop', 'david-zero-to-hero'),
        ('david-hello-goodbye',),
    ),
    'jj': (
        ('jj-second-heaven', 'jj-stories', 'jj-brave-new-world', 'jj-cao-cao', 'jj-code-89757', 'jj-from-me'),
        ('jj-she-says', 'jj-sixology', 'jj-hundred-days', 'jj-lost-n-found', 'jj-message-bottle'),
        ('jj-west-side', 'jj-like-you-do', 'jj-happily'),
        ('jj-wings',),
    ),
    'jolin': (
        ('jolin-320494434', 'jolin-1725224566', 'jolin-1446276817', 'jolin-939103165', 'jolin-339414521',
         'jolin-1725226195', 'jolin-1847591329'),
        ('jolin-558279149', 'jolin-391965228', 'jolin-332372942', 'jolin-532965325', 'jolin-1827894274'),
        ('jolin-1847591767', 'jolin-1847592410', 'jolin-1847592042'),
        ('jolin-1725224364',),
    ),
    'stefanie': (
        ('stefanie-990152978', 'stefanie-905226289', 'stefanie-298837646', 'stefanie-1443147411', 'stefanie-255921035'),
        ('stefanie-297994281', 'stefanie-255920420', 'stefanie-541825411', 'stefanie-255921008'),
        ('stefanie-1442430575', 'stefanie-1440901467'),
        ('stefanie-255923688',),
    ),
    'elva': (
        ('elva-905208168', 'elva-908296755', 'elva-905201704', 'elva-905207256', 'elva-905199475', 'elva-1712233521'),
        ('elva-905202110', 'elva-905208830', 'elva-905208037', 'elva-905214431', 'elva-908227758'),
        ('elva-905201758', 'elva-895769931', 'elva-1602620364'),
        ('elva-908317913',),
    ),
    'fish': (
        ('fish-1692291088', 'fish-1095245171', 'fish-558471475', 'fish-1097016060', 'fish-151338382', 'fish-1095630068'),
        ('fish-1462400552', 'fish-153707149', 'fish-1575900921', 'fish-152621598'),
        ('fish-1575893701', 'fish-200719550', 'fish-1686886763'),
        ('fish-151330730',),
    ),
}

CANONICAL_ALBUM_LABELS = {
    'jolin-532965325': {'name': 'Myself'},
    'stefanie-990152978': {'name': '孙燕姿', 'title': '天黑黑'},
    'stefanie-255921035': {'name': 'Stefanie'},
    'stefanie-255923688': {'name': 'Start 自选集'},
    'elva-905199475': {'name': '三面夏娃'},
}

OBSIDIAN_RATE = 0.0007


def studio_album_pool(artist, catalog):
    existing = {album['id']: album for album in catalog}
    tiers = ('gold', 'platinum', 'diamond', 'obsidian')
    masses = (0.6 * (1 - OBSIDIAN_RATE), 0.3 * (1 - OBSIDIAN_RATE),
              0.1 * (1 - OBSIDIAN_RATE), OBSIDIAN_RATE)
    pool = []
    for tier, mass, ids in zip(tiers, masses, STUDIO_ALBUMS[artist], strict=True):
        weights = [1 + .14 / (1 + exp(8 * (index / max(1, len(ids) - 1) - .5)))
                   for index in range(len(ids))]
        total = sum(weights)
        for album_id, weight in zip(ids, weights, strict=True):
            album = {**existing[album_id], **CANONICAL_ALBUM_LABELS.get(album_id, {})}
            pool.append({**album, 'tier': tier, 'probability': mass * weight / total})
    return pool
