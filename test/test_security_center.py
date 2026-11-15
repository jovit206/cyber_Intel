import unittest
from unittest.mock import MagicMock, patch
from contextlib import nullcontext

from fastapi import HTTPException

from backend.routes import security


class SecurityCenterTests(unittest.TestCase):
    def test_first_admin_bootstrap_is_explicit_and_only_promotes_existing_account(self):
        from types import SimpleNamespace
        from backend.bootstrap_security_admin import promote_first_admin
        db = MagicMock()
        db.subscription_users.count_documents.return_value = 0
        db.subscription_users.find_one.return_value = {"_id": "id-1", "user_id": "user-1"}
        db.subscription_users.update_one.return_value = SimpleNamespace(modified_count=1)
        self.assertTrue(promote_first_admin(db, "ADMIN@example.test"))
        db.subscription_users.find_one.assert_called_once_with({"email": "admin@example.test"}, {"_id": 1, "user_id": 1})
        db.subscription_users.update_one.assert_called_once()
        db.security_audit.insert_one.assert_called_once()
        db.subscription_users.count_documents.return_value = 1
        with self.assertRaises(RuntimeError):
            promote_first_admin(db, "admin@example.test")
    def test_permission_presets_are_least_privilege(self):
        matrix = {row['role']: row for row in security._permission_matrix()}
        self.assertEqual(security.ACCESS_ACTIONS['readOnly'], ['find'])
        self.assertEqual(security.ACCESS_ACTIONS['restrictedReadWrite'], ['find', 'insert', 'update'])
        self.assertFalse(matrix['MongoDB read-only']['delete'])
        self.assertFalse(matrix['MongoDB restricted writer']['delete'])
        self.assertIn('effective only', matrix['MongoDB read-only']['source'])

    def test_native_user_requires_strong_password_and_safe_identifiers(self):
        with self.assertRaises(ValueError):
            security.MongoUserCreate(username='valid_user', password='weak', database='chapter07', role_name='reader')
        with self.assertRaises(ValueError):
            security.MongoRoleCreate(role_name='reader', database='admin.system', collection='users', access='readOnly')
        with self.assertRaises(ValueError):
            security.MongoRoleCreate(role_name='systemAdmin', database='chapter07', collection='users', access='readOnly')
        user = security.MongoUserCreate(username='valid_user', password='GoodPassword1234!', database='chapter07', role_name='reader')
        self.assertNotIn('GoodPassword1234!', repr(user))

    def test_server_status_reports_disabled_auth_and_tls_as_verified(self):
        client = MagicMock()
        client.admin.command.side_effect = [
            {'parsed': {'security': {'authorization': 'disabled'}, 'net': {'tls': {'mode': 'disabled'}}}},
            {'authInfo': {'authenticatedUsers': []}},
        ]
        result = security._server_settings(client)
        self.assertEqual(result['authentication']['server_mode'], 'disabled')
        self.assertTrue(result['authentication']['verified'])
        self.assertFalse(result['authentication']['enabled'])
        self.assertEqual(result['tls']['server_mode'], 'disabled')
        self.assertTrue(result['tls']['verified'])
        self.assertFalse(result['tls']['enabled'])

    def test_tls_test_reports_unconfigured_instead_of_false_success(self):
        client = MagicMock()
        client.admin.command.side_effect = [
            {'parsed': {'security': {'authorization': 'disabled'}, 'net': {'tls': {'mode': 'disabled'}}}},
            {'authInfo': {'authenticatedUsers': []}},
        ]
        db = MagicMock()
        db.client = client
        with patch.object(security, '_admin_user', return_value={'role': 'admin'}), \
             patch.object(security, 'get_database', return_value=db):
            result = security.test_tls_connection(MagicMock())
        self.assertFalse(result['verified'])
        self.assertEqual(result['status'], 'not_configured')
        self.assertFalse(result['encrypted'])

    def test_application_role_change_is_audited_and_last_admin_is_protected(self):
        actor = {'user_id': 'admin-1', 'email': 'admin@example.test', 'role': 'admin'}
        db = MagicMock()
        db.subscription_users.find_one.return_value = {'user_id': 'user-2', 'email': 'user@example.test', 'role': 'user'}
        with patch.object(security, '_admin_user', return_value=actor), patch.object(security, 'get_database', return_value=db):
            result = security.update_application_role('user-2', security.RoleChange(role='admin'), MagicMock())
        self.assertEqual(result['role'], 'admin')
        db.subscription_users.update_one.assert_called_once()
        db.security_audit.insert_one.assert_called_once()
        db.subscription_users.find_one.return_value = {'user_id': 'admin-1', 'email': 'admin@example.test', 'role': 'admin'}
        db.subscription_users.count_documents.return_value = 1
        with patch.object(security, '_admin_user', return_value=actor), patch.object(security, 'get_database', return_value=db):
            with self.assertRaises(HTTPException) as error:
                security.update_application_role('admin-1', security.RoleChange(role='user'), MagicMock())
        self.assertEqual(error.exception.status_code, 409)

    def test_missing_native_admin_configuration_blocks_role_changes(self):
        from types import SimpleNamespace
        with patch.object(security, 'settings', SimpleNamespace(mongo_security_admin_uri='')):
            with self.assertRaises(HTTPException) as error:
                security._need_security_admin()
        self.assertEqual(error.exception.status_code, 503)


    def test_redacted_view_pipeline_excludes_salary_without_reading_values(self):
        client = MagicMock()
        database = MagicMock()
        client.__getitem__.return_value = database
        database.list_collection_names.return_value = ['employees']
        database.list_collections.return_value = iter([{
            'name': 'security_redacted_employees',
            'options': {'viewOn': 'employees', 'pipeline': [{'$project': {'salary': 0}}]},
        }])
        database.__getitem__.return_value.aggregate.return_value = iter([{'_id': 'one', 'name': 'Example'}])
        with (
            patch.object(security, '_admin_user', return_value={'role':'admin','email':'a@example.test'}),
            patch.object(security, '_security_admin_client', return_value=nullcontext(client)),
            patch.object(security, '_audit'),
            patch.object(security, 'get_database', return_value=MagicMock()),
        ):
            result = security.create_salary_redacted_view('chapter07', 'employees', MagicMock())
        self.assertTrue(result['verified'])
        self.assertEqual(result['excluded_field'], 'salary')
        database.create_collection.assert_called_once_with(
            'security_redacted_employees', viewOn='employees',
            pipeline=[{'$project': {'salary': 0}}])

    def test_tls_options_use_only_configured_trust_and_client_cert_paths(self):
        from types import SimpleNamespace
        from backend import database as db_module
        with patch.object(db_module, 'settings', SimpleNamespace(
                mongo_tls_ca_file='/run/secrets/ca.pem',
                mongo_tls_client_certificate_file='/run/secrets/client.pem')):
            options = db_module.mongo_tls_options('mongodb://mongo1:27017/?tls=true')
        self.assertEqual(options, {'tlsCAFile':'/run/secrets/ca.pem',
                                   'tlsCertificateKeyFile':'/run/secrets/client.pem'})
        self.assertNotIn('tlsAllowInvalidCertificates', options)

    def test_tls_uri_options_are_not_duplicated_by_file_settings(self):
        from types import SimpleNamespace
        from backend import database as db_module
        with patch.object(db_module, 'settings', SimpleNamespace(
                mongo_tls_ca_file='/run/secrets/ca.pem',
                mongo_tls_client_certificate_file='')):
            options = db_module.mongo_tls_options('mongodb://mongo1:27017/?tls=true&tlsCAFile=%2Fca.pem')
        self.assertEqual(options, {})

    def test_routes_register_security_capabilities(self):
        from backend.main import app
        paths=set(app.openapi()['paths'])
        for path in ('/api/security/overview','/api/security/application-users',
                     '/api/security/mongo/roles','/api/security/mongo/users',
                     '/api/security/permissions/test','/api/security/views/redacted',
                     '/api/security/tls/test','/api/security/audit'):
            self.assertIn(path, paths)

    def test_security_routes_exist_and_remain_protected(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        client = TestClient(app)
        try:
            response = client.get('/api/security/overview')
        finally:
            client.close()
        self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()

