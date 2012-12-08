#!/usr/bin/env python
#-*- coding:utf-8 -*-

"""Static file generator for Django."""
import os
import stat
import tempfile
import urlparse

import shutil

from django.utils.functional import Promise
from django.http import HttpRequest, QueryDict
from django.db.models.base import ModelBase
from django.db.models.manager import Manager
from django.db.models import Model
from django.db.models.query import QuerySet
from django.conf import settings
from django.test.client import RequestFactory
from handlers import DummyHandler


class StaticGeneratorException(Exception):
    pass


class StaticGenerator(object):
    """
    The StaticGenerator class is created for Django applications, like a blog,
    that are not updated per request.

    Usage is simple::

        from staticgenerator import quick_publish
        quick_publish('/', Post.objects.live(), FlatPage)

    The class accepts a list of 'resources' which can be any of the 
    following: URL path (string), Model (class or instance), Manager, or 
    QuerySet.

    As of v1.1, StaticGenerator includes file and path deletion::

        from staticgenerator import quick_delete
        quick_delete('/page-to-delete/')

    The most effective usage is to associate a StaticGenerator with a model's
    post_save and post_delete signal.

    The reason for having all the optional parameters is to reduce coupling
    with django in order for more effectively unit testing.
    """

    def __init__(self, *resources):
        self.resources = self.extract_resources(resources)
        self.server_name = self.get_server_name()

        try:
            self.web_root = getattr(settings, 'WEB_ROOT')
        except AttributeError:
            raise StaticGeneratorException('You must specify WEB_ROOT in settings.py')

    def extract_resources(self, resources):
        """Takes a list of resources, and gets paths by type"""
        extracted = []

        for resource in resources:

            # A URL string
            if isinstance(resource, (str, unicode, Promise)):
                extracted.append(str(resource))
                continue

            # A model instance; requires get_absolute_url method
            if isinstance(resource, Model):
                extracted.append(resource.get_absolute_url())
                continue

            # If it's a Model, we get the base Manager
            if isinstance(resource, ModelBase):
                resource = resource._default_manager

            # If it's a Manager, we get the QuerySet
            if isinstance(resource, Manager):
                resource = resource.all()

            # Append all paths from obj.get_absolute_url() to list
            if isinstance(resource, QuerySet):
                extracted += [obj.get_absolute_url() for obj in resource]

        return extracted

    def get_server_name(self):
        '''Tries to get the server name.
        First we look in the django settings.
        If it's not found we try to get it from the current Site.
        Otherwise, return "localhost".
        '''
        try:
            return getattr(settings, 'SERVER_NAME')
        except:
            pass

        try:
            from django.contrib.sites.models import Site
            return Site.objects.get_current().domain
        except:
            print '*** Warning ***: Using "localhost" for domain name. Use django.contrib.sites or set settings.SERVER_NAME to disable this warning.'
            return 'localhost'

    def get_content_from_path(self, path):
        """
        Imitates a basic http request using DummyHandler to retrieve
        resulting output (HTML, XML, whatever)
        """

        request = RequestFactory().get(path)
        # We must parse the path to grab query string
        parsed = urlparse.urlparse(path)
        request.path_info = parsed.path
        request.GET = QueryDict(parsed.query)
        request.META.setdefault('SERVER_PORT', 80)
        request.META.setdefault('SERVER_NAME', self.server_name)
        request.META.setdefault('REMOTE_ADDR', '127.0.0.1')

        handler = DummyHandler()
        try:
            response = handler(request)
        except Exception, err:
            raise StaticGeneratorException("The requested page(\"%s\") raised an exception. Static Generation failed. Error: %s" % (path, str(err)))

        if int(response.status_code) != 200:
            raise StaticGeneratorException("The requested page(\"%s\") returned http code %d. Static Generation failed." % (path, int(response.status_code)))

        return response.content

    def get_query_string_from_path(self, path):
        parts = path.split('?')
        if len(parts) == 1:
            return parts[0], None
        if len(parts) > 2:
            raise StaticGeneratorException('Path %s has multiple query string values' % path)
        return parts[0], parts[1]

    def get_filename_from_path(self, path, query_string, is_ajax=False):
        """
        Returns (filename, directory). None if unable to cache this request.
        Creates index.html for path if necessary
        """
        if path.endswith('/'):
            # Always include a %3F in the file name, even if there are no query
            # parameters.  Using %3F instead of a question mark makes rewriting
            # possible in Apache.  Always including it makes rewriting easier.
            path = '%sindex.html%%3F' % path
        # will not work on windows... meh
        if query_string:
            path += query_string
        if is_ajax:
            # Append an ',ajax' suffix to the file name for AJAX requests.
            # This makes it possible to cache responses which have different
            # content for AJAX requests.
            path += ',ajax'

        filename = os.path.join(self.web_root, path.lstrip('/')).encode('utf-8')
        if len(filename) > 255:
            return None, None
        return filename, os.path.dirname(filename)

    def publish_from_path(self, path, query_string=None, content=None, is_ajax=False):
        """
        Gets filename and content for a path, attempts to create directory if 
        necessary, writes to file.
        """
        content_path = path
        # The query_string parameter is only passed from the
        # middleware. If we're generating a page from, e.g.,
        # the `quick_publish` function, the path may still
        # have a query string component.
        if query_string is None:
            path, query_string = self.get_query_string_from_path(path)
        filename, directory = self.get_filename_from_path(
            path, query_string, is_ajax=is_ajax)
        if not filename:
            return # cannot cache
        if not content:
            content = self.get_content_from_path(content_path)

        if not os.path.exists(directory):
            try:
                os.makedirs(directory)
            except:
                raise StaticGeneratorException('Could not create the directory: %s' % directory)

        try:
            f, tmpname = tempfile.mkstemp(dir=directory)
            os.write(f, content)
            os.close(f)
            os.chmod(tmpname, stat.S_IREAD | stat.S_IWRITE | stat.S_IWUSR | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            os.rename(tmpname, filename)
        except:
            raise StaticGeneratorException('Could not create the file: %s' % filename)

    def recursive_delete_from_path(self, path):
        filename, directory = self.get_filename_from_path(path, '')
        shutil.rmtree(directory, True)

    def delete_from_path(self, path, is_ajax=False):
        """Deletes file, attempts to delete directory"""
        path, query_string = self.get_query_string_from_path(path)
        filename, directory = self.get_filename_from_path(
            path, query_string, is_ajax=is_ajax)

        try:
            if os.path.exists(filename):
                os.remove(filename)
        except:
            raise StaticGeneratorException('Could not delete file: %s' % filename)

        try:
            os.rmdir(directory)
        except OSError:
            # Will fail if a directory is not empty, in which case we don't
            # want to delete it anyway
            pass

    def do_all(self, func):
        return [func(path) for path in self.resources]

    def delete(self):
        return self.do_all(self.delete_from_path)

    def recursive_delete(self):
        return self.do_all(self.recursive_delete_from_path)

    def publish(self):
        return self.do_all(self.publish_from_path)

def quick_publish(*resources):
    return StaticGenerator(*resources).publish()

def quick_delete(*resources):
    return StaticGenerator(*resources).delete()

def recursive_delete(*resources):
    return StaticGenerator(*resources).recursive_delete()
