#
# Copyright (C) 2007-2010 by Johan De Taeye, frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser
# General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA
#

# file : $URL$
# revision : $LastChangedRevision$  $LastChangedBy$
# date : $LastChangedDate$

r'''
An reusable application that contains common functionality of different
frePPLe data models.

The common functionality handles:
  - user preferences: reporting buckets, report start and end dates, language, csv delimiter
  - breadcrumbs
  - login using the e-mail address
  - generic report framework
  - database utility functions, mainly to handle SQL dates in a portable way
  - date and time bucket definition
  - middelware allowing users to set their preferred language
'''

from django import template
from django.core.exceptions import PermissionDenied
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404, render_to_response
from django.contrib.admin.util import unquote
from django.utils.encoding import force_unicode
from django.utils.html import escape
from django.db import transaction
from django.utils.translation import ugettext_lazy as _
from django.utils.text import capfirst, get_text_list
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from django.contrib.admin.util import get_deleted_objects

# Make our tags built-in, so we don't have to load them any more in our
# templates with a 'load' tag.
template.add_to_builtins('freppledb.common.templatetags.base_utils')
 
csrf_protect_m = method_decorator(csrf_protect)

class MultiDBModelAdmin(admin.ModelAdmin):
  r'''
  This class is an enhanced version of the django regular admin model.
  It adds:
     - support for multiple databases
          - store and load history information in the right database
          - assure prefix is maintained in the URLs
          - check for related objects in the right database
     - different logic to determine the next page to display

  See the standard code in the file django\contrib\admin\options.py
  The level of customization is relatively high, and this code is a bit of a 
  concern for future upgrades of Django...
  ''' 
  
  def save_model(self, request, obj, form, change):
    # Tell Django to save objects to the 'other' database.
    obj.save(using=request.database)

  def queryset(self, request):
    return super(MultiDBModelAdmin, self).queryset(request).using(request.database)

  def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
    return super(MultiDBModelAdmin, self).formfield_for_foreignkey(db_field, request=request, using=request.database, **kwargs)

  def formfield_for_manytomany(self, db_field, request=None, **kwargs):
    return super(MultiDBModelAdmin, self).formfield_for_manytomany(db_field, request=request, using=request.database, **kwargs)

  def log_addition(self, request, object):
    """
    Log that an object has been successfully added.
    """
    from django.contrib.admin.models import LogEntry, ADDITION
    LogEntry(
        user_id         = request.user.pk,
        content_type_id = ContentType.objects.get_for_model(object).pk,
        object_id       = force_unicode(object.pk),
        object_repr     = force_unicode(object)[:200],
        action_flag     = ADDITION
    ).save(using=request.database)

  def log_change(self, request, object, message):
    """
    Log that an object has been successfully changed.
    """
    from django.contrib.admin.models import LogEntry, CHANGE
    LogEntry(
        user_id         = request.user.pk,
        content_type_id = ContentType.objects.get_for_model(object).pk,
        object_id       = force_unicode(object.pk),
        object_repr     = force_unicode(object)[:200],
        action_flag     = CHANGE,
        change_message  = message
    ).save(using=request.database)

  def log_deletion(self, request, object, object_repr):
    """
    Log that an object has been successfully deleted. Note that since the
    object is deleted, it might no longer be safe to call *any* methods
    on the object, hence this method getting object_repr.
    """
    from django.contrib.admin.models import LogEntry, DELETION
    LogEntry(
        user_id         = request.user.id,
        content_type_id = ContentType.objects.get_for_model(self.model).pk,
        object_id       = force_unicode(object.pk),
        object_repr     = object_repr[:200],
        action_flag     = DELETION
    ).save(using=request.database)

  def history_view(self, request, object_id, extra_context=None):
    "The 'history' admin view for this model."
    from django.contrib.admin.models import LogEntry
    model = self.model
    opts = model._meta
    app_label = opts.app_label
    action_list = LogEntry.objects.using(request.database).filter(
      object_id = object_id,
      content_type__id__exact = ContentType.objects.get_for_model(model).id
    ).select_related().order_by('action_time')
    # If no history was found, see whether this object even exists.
    obj = get_object_or_404(model.objects.using(request.database), pk=unquote(object_id))
    context = {
      'title': _('Change history: %s') % force_unicode(obj),
      'action_list': action_list,
      'module_name': capfirst(force_unicode(opts.verbose_name_plural)),
      'object': obj,
      'root_path': self.admin_site.root_path,
      'app_label': app_label,
    }
    context.update(extra_context or {})
    context_instance = template.RequestContext(request, current_app=self.admin_site.name)
    return render_to_response(self.object_history_template or [
      "admin/%s/%s/object_history.html" % (app_label, opts.object_name.lower()),
      "admin/%s/object_history.html" % app_label,
      "admin/object_history.html"
    ], context, context_instance=context_instance)
      
  def response_add(self, request, obj, post_url_continue='../%s/'):
    """
    Determines the HttpResponse for the add_view stage.
    """
    opts = obj._meta
    pk_value = obj._get_pk_val()

    msg = _('The %(name)s "%(obj)s" was added successfully.') % {'name': force_unicode(opts.verbose_name), 'obj': force_unicode(obj)}
    # Here, we distinguish between different save types by checking for
    # the presence of keys in request.POST.
    if "_continue" in request.POST:
      self.message_user(request, msg + ' ' + force_unicode(_("You may edit it again below.")))
      if "_popup" in request.POST:
        post_url_continue += "?_popup=1"
      return HttpResponseRedirect(post_url_continue % pk_value)

    if "_popup" in request.POST:
      return HttpResponse('<script type="text/javascript">opener.dismissAddAnotherPopup(window, "%s", "%s");</script>' % \
          # escape() calls force_unicode.
          (escape(pk_value), escapejs(obj)))
    elif "_addanother" in request.POST:
      self.message_user(request, msg + ' ' + force_unicode(_("You may add another %s below.") % force_unicode(opts.verbose_name)))
      return HttpResponseRedirect(request.prefix + request.path)
    else:
      self.message_user(request, msg)

      # Redirect to previous crumb
      return HttpResponseRedirect(request.session['crumbs'][-2][2])


  def response_change(self, request, obj):
    """
    Determines the HttpResponse for the change_view stage.
    """
    opts = obj._meta

    # Handle proxy models automatically created by .only() or .defer()
    verbose_name = opts.verbose_name
    if obj._deferred:
      opts_ = opts.proxy_for_model._meta
      verbose_name = opts_.verbose_name

    pk_value = obj._get_pk_val()

    msg = _('The %(name)s "%(obj)s" was changed successfully.') % {'name': force_unicode(verbose_name), 'obj': force_unicode(obj)}
    if "_continue" in request.POST:
      self.message_user(request, msg + ' ' + force_unicode(_("You may edit it again below.")))
      if "_popup" in request.REQUEST:
          return HttpResponseRedirect(request.prefix + request.path + "?_popup=1")
      else:
          return HttpResponseRedirect(request.prefix + request.path)
    elif "_saveasnew" in request.POST:
      msg = _('The %(name)s "%(obj)s" was added successfully. You may edit it again below.') % {'name': force_unicode(verbose_name), 'obj': obj}
      self.message_user(request, msg)
      return HttpResponseRedirect("../%s/" % pk_value)
    elif "_addanother" in request.POST:
      self.message_user(request, msg + ' ' + force_unicode(_("You may add another %s below.") % force_unicode(verbose_name)))
      return HttpResponseRedirect("../add/")
    else:
      self.message_user(request, msg)
      # Redirect to previous crumb
      return HttpResponseRedirect(request.session['crumbs'][-2][2])

  @csrf_protect_m
  @transaction.commit_on_success
  def delete_view(self, request, object_id, extra_context=None):
    "The 'delete' admin view for this model."
    opts = self.model._meta
    app_label = opts.app_label

    obj = self.get_object(request, unquote(object_id))

    if not self.has_delete_permission(request, obj):
      raise PermissionDenied

    if obj is None:
      raise Http404(_('%(name)s object with primary key %(key)r does not exist.') % {'name': force_unicode(opts.verbose_name), 'key': escape(object_id)})

    using = request.database

    # Populate deleted_objects, a data structure of all related objects that
    # will also be deleted.
    (deleted_objects, perms_needed, protected) = get_deleted_objects(
       [obj], opts, request.user, self.admin_site, using)
    
    if request.POST: # The user has already confirmed the deletion.
      if perms_needed:
        raise PermissionDenied
      obj_display = force_unicode(obj)
      self.log_deletion(request, obj, obj_display)
      self.delete_model(request, obj)

      self.message_user(request, _('The %(name)s "%(obj)s" was deleted successfully.') % {'name': force_unicode(opts.verbose_name), 'obj': force_unicode(obj_display)})

      # Redirect to previous crumb
      return HttpResponseRedirect(request.session['crumbs'][-3][2])

    object_name = force_unicode(opts.verbose_name)

    if perms_needed or protected:
      title = _("Cannot delete %(name)s") % {"name": object_name}
    else:
      title = _("Are you sure?")
    
    context = {
        "title": title,
        "object_name": object_name,
        "object": obj,
        "deleted_objects": deleted_objects,
        "perms_lacking": perms_needed,
        "protected": protected,
        "opts": opts,
        "root_path": self.admin_site.root_path,
        "app_label": app_label,
    }
    context.update(extra_context or {})
    context_instance = template.RequestContext(request, current_app=self.admin_site.name)
    return render_to_response(self.delete_confirmation_template or [
        "admin/%s/%s/delete_confirmation.html" % (app_label, opts.object_name.lower()),
        "admin/%s/delete_confirmation.html" % app_label,
        "admin/delete_confirmation.html"
    ], context, context_instance=context_instance)
                            
  # TODO: allow permissions per schema
  # def has_add_permission(self, request):
  # def has_change_permission(self, request, obj=None):
  # def has_delete_permission(self, request, obj=None):


class MultiDBTabularInline(admin.TabularInline):

  def queryset(self, request):
    return super(MultiDBTabularInline, self).queryset(request).using(request.database)

  def formfield_for_foreignkey(self, db_field, request=None, **kwargs):
    return super(MultiDBTabularInline, self).formfield_for_foreignkey(db_field, request=request, using=request.database, **kwargs)

  def formfield_for_manytomany(self, db_field, request=None, **kwargs):
    return super(MultiDBTabularInline, self).formfield_for_manytomany(db_field, request=request, using=request.database, **kwargs)
  