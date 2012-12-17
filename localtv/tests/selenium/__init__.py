# Miro Community - Easiest way to make a video website
#
# Copyright (C) 2010, 2011, 2012 Participatory Culture Foundation
#
# Miro Community is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version.
#
# Miro Community is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Miro Community.  If not, see <http://www.gnu.org/licenses/>.

import time
import os
from django.core import management
from django.test import LiveServerTestCase
from localtv.tests import BaseTestCase
from selenium import webdriver
from django.conf import settings


class WebdriverTestCase(LiveServerTestCase, BaseTestCase):
    @classmethod
    def setUpClass(cls):
        super(WebdriverTestCase, cls).setUpClass()
        cls.results_dir = getattr(settings, "TEST_RESULTS_DIR")
        if not os.path.exists(cls.results_dir):
            os.makedirs(cls.results_dir)

    def setUp(self):
        super(WebdriverTestCase, self).setUp()
        self._clear_index()
        LiveServerTestCase.setUp(self)
        setattr(self, 'base_url', self.live_server_url + '/')
        self.browser = getattr(webdriver, getattr(settings, 'TEST_BROWSER'))()
        BaseTestCase.setUp(self)
        self.admin_user = 'seleniumTestAdmin' 
        self.admin_pass = 'password'
        self.normal_user = 'seleniumTestUser'
        self.normal_pass = 'password'
        self.create_user(username=self.admin_user,
                         password=self.admin_pass, is_superuser=True)
        self.create_user(username=self.normal_user, password=self.normal_pass)
        self.base_url = self.live_server_url + '/'
        self.browser.get(self.base_url)

    def tearDown(self):
        time.sleep(1)
        try:
            screenshot_name = "%s.png" % self.id()
            filename = os.path.join(self.results_dir, screenshot_name)
            self.browser.get_screenshot_as_file(filename)
        finally:
            self.browser.quit()