"""Exercise the installed Discord admission gate without networking or inference."""
import sys
from types import SimpleNamespace

sys.path.insert(0, '/opt/hermes/plugins/platforms/discord')
import adapter
from gateway.config import PlatformConfig

owner = '447602825082437632'
native = adapter.DiscordAdapter(PlatformConfig())
native._allowed_user_ids = native._get_allowed_users()
native._allowed_role_ids = native._get_allowed_roles()
assert native._allowed_user_ids == {owner}, 'Expected sole owner allowlist'
assert not native._allowed_role_ids, 'Role grants must be empty'
assert not native._discord_allow_all_users()
assert not native._gateway_allow_all_users()
native._client = SimpleNamespace(user=SimpleNamespace(id=1565453125584687214))
assert native._is_allowed_user(owner, is_dm=True)
for guild in (None, SimpleNamespace(id=1565454238203519096)):
    message = SimpleNamespace(
        id=999999999999999999,
        author=SimpleNamespace(id=999999999999999998, bot=False),
        type=adapter.discord.MessageType.default,
        guild=guild,
        channel=SimpleNamespace(id=1565784164547035238),
        content='Do not execute: unauthorized admission fixture',
    )
    assert native._discord_message_admission(message, claim=False) == (False, False)
print('PASS: sole owner configured; unlisted DM and server events rejected at native admission')
