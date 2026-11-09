import base64
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('jetson_network', Path(__file__).resolve().parents[1] / 'deploy' / 'jetson-network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class NetworkTests(unittest.TestCase):
    def test_healthy_profiles_are_not_rewritten_or_reconnected(self):
        values = {'connection.permissions': '', 'connection.autoconnect': 'yes',
                  'connection.autoconnect-retries': '3', '802-11-wireless.powersave': '2'}
        with patch.object(network, 'output', return_value='wifi:802-11-wireless\nusb:bridge'), \
             patch.object(network, 'profile_value', side_effect=lambda uuid, key: values[key]), \
             patch.object(network, 'run') as run:
            self.assertEqual(network.configure_known(), ['wifi'])
            run.assert_not_called()

    def test_user_only_profile_becomes_available_without_login(self):
        values = {'connection.permissions': 'user:armanberkay', 'connection.autoconnect': 'yes',
                  'connection.autoconnect-retries': '3', '802-11-wireless.powersave': '2'}
        with patch.object(network, 'output', return_value='wifi:802-11-wireless'), \
             patch.object(network, 'profile_value', side_effect=lambda uuid, key: values[key]), \
             patch.object(network, 'run') as run:
            network.configure_known()
            run.assert_called_once_with('nmcli', 'connection', 'modify', 'uuid', 'wifi', 'connection.permissions', '')

    def test_ubuntu_enum_rendering_does_not_rewrite_a_healthy_profile(self):
        values = {'connection.permissions': '', 'connection.autoconnect': 'yes',
                  'connection.autoconnect-retries': '3', '802-11-wireless.powersave': 'disable'}
        with patch.object(network, 'output', return_value='wifi:802-11-wireless'), \
             patch.object(network, 'profile_value', side_effect=lambda uuid, key: values[key]), \
             patch.object(network, 'run') as run:
            network.configure_known()
            run.assert_not_called()

    def test_ssid_is_validated_and_remains_literal(self):
        ssid = "Lab's $(not-a-command)"
        self.assertEqual(network.decode_ssid(base64.b64encode(ssid.encode()).decode()), ssid)
        for value in ('', 'x' * 33, 'Lab\nother', 'Lab\x00other'):
            with self.assertRaises(ValueError):
                network.decode_ssid(base64.b64encode(value.encode()).decode())

    def test_sharing_is_opt_in(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(network, 'SHARING', Path(folder) / 'disabled'), \
             patch.object(network, 'prepare_usb') as prepare, \
             patch.object(network, 'sharing_gateway_ready') as connect:
            self.assertFalse(network.recover_sharing())
            prepare.assert_not_called()
            connect.assert_not_called()

    def test_absent_laptop_does_not_install_a_dead_default_route(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'enabled'
            marker.touch()
            with patch.object(network, 'SHARING', marker), \
                 patch.object(network, 'prepare_usb'), \
                 patch.object(network, 'sharing_gateway_ready', return_value=False), \
                 patch.object(network, 'output', return_value='default via 10.0.0.1 dev wlan0 metric 600'), \
                 patch.object(network, 'run') as run:
                self.assertFalse(network.recover_sharing())
                run.assert_not_called()

    def test_sharing_requires_a_valid_dns_reply_from_the_laptop(self):
        with patch.object(network.os, 'urandom', return_value=b'ID'), \
             patch.object(network.socket, 'socket') as factory:
            client = factory.return_value.__enter__.return_value
            valid = b'ID\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00'
            client.recvfrom.return_value = (valid, ('192.168.137.1', 53))
            self.assertTrue(network.sharing_gateway_ready())
            client.recvfrom.return_value = (valid, ('192.168.137.99', 53))
            self.assertFalse(network.sharing_gateway_ready())
            client.recvfrom.return_value = (b'NO' + valid[2:], ('192.168.137.1', 53))
            self.assertFalse(network.sharing_gateway_ready())

    def test_usb_subnet_conflict_does_not_change_interfaces(self):
        addresses = [{'ifname': 'l4tbr0', 'addr_info': [{'local': '192.168.55.1'}]},
                     {'ifname': 'wlan0', 'addr_info': [{'local': '192.168.137.50'}]}]
        with patch.object(network, 'output', return_value=json.dumps(addresses)), \
             patch.object(network, 'run') as run:
            with self.assertRaises(RuntimeError):
                network.prepare_usb()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
