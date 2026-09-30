from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from helpdesk import hooks
from helpdesk.search_sqlite import HelpdeskSearch, build_index
from helpdesk.test_utils import create_user, make_ticket

RESTRICTED_USER = "helpdesk-search-user@example.com"


class TestSearchPermissionFilter(FrappeTestCase):
    """Under PREFILTER_LIMIT the permission filter binds exactly; over it,
    _drop_unpermitted gates the results instead."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # HD Customer grants doctype-level read; row visibility still comes from
        # the permission query. A role-less user cannot get_list at all.
        create_user(RESTRICTED_USER).add_roles("HD Customer")
        cls.own_ticket = make_ticket(
            subject="Search perm own", raised_by=RESTRICTED_USER
        )
        cls.other_ticket = make_ticket(subject="Search perm other")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_small_sites_bind_an_exact_prefilter(self):
        search = HelpdeskSearch()
        filters = search.get_search_filters()

        self.assertIn(self.own_ticket.name, filters["reference_ticket"])
        self.assertFalse(search.is_post_filter_required)

    def test_large_sites_skip_the_prefilter_and_flag_post_filtering(self):
        search = HelpdeskSearch()
        with patch("helpdesk.search_sqlite.PREFILTER_LIMIT", 1):
            filters = search.get_search_filters()

        self.assertEqual(filters, {})
        self.assertTrue(search.is_post_filter_required)

    def test_post_filter_drops_tickets_the_user_cannot_see(self):
        rows = [
            {"reference_ticket": self.own_ticket.name},
            {"reference_ticket": self.other_ticket.name},
            # Communication rows carry the ticket in reference_name only.
            {"reference_ticket": None, "reference_name": self.own_ticket.name},
            # No resolvable ticket: must fail closed.
            {"reference_ticket": None, "reference_name": None},
        ]

        frappe.set_user(RESTRICTED_USER)
        kept = HelpdeskSearch()._drop_unpermitted(rows)

        self.assertEqual(
            [HelpdeskSearch._ticket_of(r) for r in kept],
            [self.own_ticket.name, self.own_ticket.name],
        )

    def test_filter_options_bind_a_constant_number_of_variables(self):
        """Runs the json_each facets query for real."""
        options = HelpdeskSearch().get_filter_options()

        self.assertEqual(
            set(options), {"teams", "statuses", "priorities", "customers", "doctypes"}
        )


class TestSearchLifecycle(TestCase):
    def test_hooks_delegate_index_lifecycle_to_frappe_sqlite_search(self):
        self.assertEqual(
            hooks.sqlite_search, ["helpdesk.search_sqlite.HelpdeskSearch"]
        )
        configured_events = {
            event
            for events in hooks.scheduler_events.values()
            for event in events
        }
        self.assertFalse(
            any(event.startswith("helpdesk.search.") for event in configured_events)
        )
        self.assertFalse(hasattr(hooks, "after_migrate"))

    def test_manual_rebuild_uses_whitelisted_sqlite_builder(self):
        self.assertIn(build_index, frappe.whitelisted)
        with patch("helpdesk.search_sqlite.HelpdeskSearch") as search_class:
            build_index()

        search_class.return_value.build_index.assert_called_once_with()
