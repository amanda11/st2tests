from __future__ import absolute_import, print_function, unicode_literals

import os
import time
import unittest2

from slackclient import SlackClient


# REQUIRED environment variables:
#
# - SLACK_CHANNEL
# - SLACK_BOT_USERNAME
# - SLACK_USER_API_TOKEN
# - SLACK_USER_USERNAME

# OPTIONAL environment variables:
#
# - SLACK_WAIT_FOR_MESSAGES_TIMEOUT
# - SLACK_DONT_WAIT_FOR_MESSAGES_TIMEOUT


WAIT_FOR_MESSAGES_TIMEOUT = os.environ.get('SLACK_WAIT_FOR_MESSAGES_TIMEOUT', 120)

DONT_WAIT_FOR_MESSAGES_TIMEOUT = os.environ.get('SLACK_DONT_WAIT_FOR_MESSAGES_TIMEOUT', 8)

SLACK_CHANNEL = os.environ['SLACK_CHANNEL']
SLACK_BOT_USERNAME = os.environ['SLACK_BOT_USERNAME']
SLACK_USER_API_TOKEN = os.environ['SLACK_USER_API_TOKEN']
SLACK_USER_USERNAME = os.environ['SLACK_USER_USERNAME']


def ignore_username(userid):
    # Remove 'user_typing' messages, since they are almost certainly
    # caused by a human typing in the channel. Otherwise, the number of
    # messages can be erroneously inflated.
    def filter_messages(message):
        if message['type'] == 'user_typing':
            return False
        elif message['type'] == 'hello':
            return False
        elif message.get('user') == userid:
            return False
        else:
            return True
    return filter_messages


class SlackEndToEndTestCase(unittest2.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        # This token is for the bot that impersonates a user
        cls.client = SlackClient(connect=True, token=SLACK_USER_API_TOKEN)
        cls.channel = SLACK_CHANNEL
        cls.bot_username = SLACK_BOT_USERNAME
        cls.username = SLACK_USER_USERNAME
        cls.userid = cls.get_user_id(cls.username)
        cls.filter = staticmethod(ignore_username(cls.userid))

        cls.client.api_call(
            "chat.postMessage",
            channel=cls.channel,
            text="`===== BEGINNING ChatOps End-to-End Tests =====`",
            as_user=True)

        time.sleep(DONT_WAIT_FOR_MESSAGES_TIMEOUT/4)

    @classmethod
    def tearDownClass(cls):
        cls.client.api_call(
            "chat.postMessage",
            channel=cls.channel,
            text="`===== FINISHED ChatOps End-to-End Tests =====`",
            as_user=True)

    @classmethod
    def get_user_id(cls, username):
        for user in cls.client.api_call("users.list").get('members'):
            if user.get('real_name') == username:
                return user.get('id')

    def setUp(self):
        time.sleep(DONT_WAIT_FOR_MESSAGES_TIMEOUT/4)

        # Connect as the bot
        self.client.rtm_connect()

        # Drain the event buffer
        self.client.rtm_read()

    def tearDown(self):
        time.sleep(DONT_WAIT_FOR_MESSAGES_TIMEOUT/4)

        # Drain the event buffer
        self.client.rtm_read()

    def test_non_response(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="This message should not prompt a response from the bot",
            as_user=True)

        messages = []
        for i in range(DONT_WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertListEqual(messages, [])

    def test_help_shortcut(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!help",
            as_user=True)

        messages = []
        for i in range(DONT_WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 1:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(1, len(messages))
        # Help commands don't get acked
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("!help - Displays all of the help commands that this bot knows about.", messages[0]['text'])

    def test_help_longcut(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="@{bot_user}help".format(bot_user=self.bot_username),
            as_user=True,
            link_names=True)

        messages = []
        for i in range(DONT_WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 1:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(1, len(messages))
        # Help commands don't get acked
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("!help - Displays all of the help commands that this bot knows about.", messages[0]['text'])

    def test_run_command_on_localhost(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!run date on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_run_exact_command_on_localhost(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!run \"echo ChatOps run exact command on localhost\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        # This test depends a bit on the hubot-stackstorm adapter
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_run_exact_command_on_multiple_hosts(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!run \"echo ChatOps run exact command on multiple hosts\" on localhost,127.0.0.1",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_run_command_on_default_hosts(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!default run \"echo ChatOps run command on default hosts\"",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_run_command_with_regex_and_default_parameter(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!regex run \"echo ChatOps run command on default hosts\".",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_execute_command_with_regex_and_default_parameter(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!regex execute \"echo ChatOps run command on default hosts\"!",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_run_command_with_extra_parameter(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!extra run \"echo ChatOps run command on default hosts\" on localhost timeout=120",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_weird_run_remote_command_with_parameter(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!weird run remote command \"echo ChatOps run command on default hosts\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_weird_run_remote_command_with_ssh(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!weird ssh to hosts localhost and run command \"echo ChatOps run command on default hosts\"",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_weird_omg_just_run_command(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!weird OMG st2 just run this command \"echo ChatOps run command on default hosts\" on ma boxes localhost already",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_custom_ack(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!custom-ack run \"echo ChatOps run command on default hosts\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 1:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(1, len(messages))

        # Test for response
        self.assertEqual('message', messages[0]['type'])
        self.assertIsNotNone(messages[0].get('bot_id'))
        self.assertEqual(messages[0].get('text'), 'Running the command(s) for you')

    def test_disabled_ack(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!disabled-custom-ack run \"echo ChatOps run command on default hosts\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 1:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(1, len(messages))

        # Test for response
        self.assertEqual('message', messages[0]['type'])
        self.assertIsNotNone(messages[0].get('bot_id'))
        self.assertIsNotNone(messages[0].get('attachments'))
        self.assertGreater(len(messages[0]['attachments']), 0)
        self.assertIsNotNone(messages[0]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[0]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[0]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

    def test_disabled_ack_with_bad_command(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!disabled-custom-ack run \"echof ChatOps run command on default hosts\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 1:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(1, len(messages))

        # Test for response
        self.assertEqual('message', messages[0]['type'])
        self.assertIsNotNone(messages[0].get('bot_id'))
        self.assertIsNotNone(messages[0].get('attachments'))
        self.assertGreater(len(messages[0]['attachments']), 0)
        self.assertIsNotNone(messages[0]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[0]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test attachment
        msg_text = messages[0]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.remote completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*failed')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')
        self.assertRegex(msg_text, r'stderr\s*:\s*bash: echof: command not found')
        self.assertRegex(msg_text, r'return_code\s*:\s*\d+')

    def test_alias_with_custom_result_format(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!custom-format run \"echo ChatOps run command on single host\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test fallback
        self.assertEqual(messages[1]['attachments'][0]['text'],
                         messages[1]['attachments'][0]['fallback'])

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        expected_text = ('Ran command `echo ChatOps run command on single host` on `1` host.\n'
                         '\n'
                         'Details are as follows:\n'
                         'Host: `localhost`\n'
                         '    ---&gt; stdout: ChatOps run command on single host\n'
                         '    ---&gt; stderr: \n')
        self.assertEqual(msg_text, expected_text)

    def test_alias_with_custom_result_format_and_multiple_hosts(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!custom-format run \"echo ChatOps run command on multiple hosts\" on localhost,127.0.0.1",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertRegex(messages[1]['attachments'][0]['pretext'], r'<@{userid}>'.format(userid=self.userid))

        # Test fallback
        self.assertEqual(messages[1]['attachments'][0]['text'],
                         messages[1]['attachments'][0]['fallback'])

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        expected_text = ('Ran command `echo ChatOps run command on multiple hosts` on `2` hosts.\n'
                         '\n'
                         'Details are as follows:\n'
                         'Host: `127.0.0.1`\n'
                         '    ---&gt; stdout: ChatOps run command on multiple hosts\n'
                         '    ---&gt; stderr: \n'
                         'Host: `localhost`\n'
                         '    ---&gt; stdout: ChatOps run command on multiple hosts\n'
                         '    ---&gt; stderr: \n')
        self.assertEqual(msg_text, expected_text)

    def test_alias_with_disabled_result(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!disabled-result run \"echo ChatOps run command on default hosts\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        time.sleep(DONT_WAIT_FOR_MESSAGES_TIMEOUT/2)

        self.assertEqual(1, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

    def test_attachment_and_plaintext_backup(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!plaintext-and-attachment run \"echo ChatOps run exact command with custom result format with plaintext and attachment\" on localhost",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertEqual(messages[1]['attachments'][0]['pretext'], '<@{userid}>: action completed! '.format(userid=self.userid))

        # Test attachment
        self.assertEqual(messages[1]['attachments'][0]['fallback'],
                         messages[1]['attachments'][0]['text'])

    def test_fields_parameter(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!kitten pic",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertEqual('message', messages[0]['type'])
        self.assertIn("details available at", messages[0]['text'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertEqual(messages[1]['attachments'][0]['pretext'], r'<@{userid}>: your kittens are here! '.format(userid=self.userid))

        # Test fallback
        self.assertEqual(messages[1]['attachments'][0]['fallback'],
                         messages[1]['attachments'][0]['text'])

        # Test attachment
        self.assertEqual(messages[1]['attachments'][0]['text'], ' Regards from the Box Kingdom.')
        self.assertEqual(messages[1]['attachments'][0]['fields'],
                         [
                            {
                                'short': True,
                                'title': 'Kitten headcount',
                                'value': 'Eight.',
                            },
                            {
                                'short': True,
                                'title': 'Number of boxes',
                                'value': 'A bunch',
                            },
                         ])
        self.assertEqual(messages[1]['attachments'][0]['image_url'], 'http://i.imgur.com/Gb9kAYK.jpg')
        self.assertEqual(messages[1]['attachments'][0]['color'], '00AA00')

    def test_jinja_input_parameters(self):
        post_message_response = self.client.api_call(
            "chat.postMessage",
            channel=self.channel,
            text="!say Hello in #88CCEE",
            as_user=True)

        messages = []
        for i in range(WAIT_FOR_MESSAGES_TIMEOUT):
            if len(messages) >= 2:
                break
            time.sleep(1)

            all_messages = self.client.rtm_read()

            filtered_messages = filter(self.filter, all_messages)

            if filtered_messages:
                messages.extend(filtered_messages)

        self.assertEqual(2, len(messages))

        # Test for ack
        self.assertIn("details available at", messages[0]['text'])
        self.assertEqual('message', messages[0]['type'])

        # Test for response
        self.assertEqual('message', messages[1]['type'])
        self.assertIsNotNone(messages[1].get('bot_id'))
        self.assertIsNotNone(messages[1].get('attachments'))
        self.assertGreater(len(messages[1]['attachments']), 0)
        self.assertIsNotNone(messages[1]['attachments'][0].get('text'))

        # Check the pretext
        self.assertEqual(messages[1]['attachments'][0]['pretext'], r'<@{userid}>: '.format(userid=self.userid))

        # Test fallback
        self.assertEqual(messages[1]['attachments'][0]['fallback'],
                         messages[1]['attachments'][0]['text'])

        # Test attachment
        msg_text = messages[1]['attachments'][0]['text']
        self.assertRegex(msg_text, r'Action core\.noop completed\.')
        self.assertRegex(msg_text, r'status\s*:\s*succeeded')
        self.assertRegex(msg_text, r'execution\s*:\s*[0-9a-fA-F]{24}')
        # The time can be an integer or a float, and might contain non-ASCII
        # characters like mu (Unicode 03BC), which gets converted to \u03BC.
        # So instead of strictly specifying those, we have a very relaxed
        # regex to capture the execution duration.
        self.assertRegex(msg_text, r'Took \d+.*s to complete\.')

        self.assertEqual(messages[1]['attachments'][0]['color'], '88CCEE')


try:
    from st2common.runners.base_action import Action

    class SlackEndToEndTestCase(Action):
        def run(self):
            return unittest2.main(exit=False)

except ImportError:
    pass


if __name__ == '__main__':
    unittest2.main()