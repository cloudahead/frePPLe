# -*- coding: utf-8 -*-
#
# Copyright (C) 2014-2016 by frePPLe bvba
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
import logging
import openerp
from werkzeug.exceptions import MethodNotAllowed, InternalServerError
from werkzeug.wrappers import Response

from openerp.addons.web.controllers.main import db_monodb, ensure_db

from openerp.addons.frepple.controllers.outbound import exporter
from openerp.addons.frepple.controllers.inbound import importer

try:
   import jwt
except:
   logger.error('PyJWT module has not been installed. Please install the library from https://pypi.python.org/pypi/PyJWT')


logger = logging.getLogger(__name__)


class XMLController(openerp.http.Controller):
  
    def authenticate(self, req, database, language=None):
        '''
        Implements HTTP basic authentication.
        TODO Authentication using a webtoken instead (or additional).
        '''
        if not 'authorization' in req.httprequest.headers:
            raise Exception("No authentication header")
        authmeth, auth = req.httprequest.headers['authorization'].split(' ', 1)
        if authmeth.lower() != 'basic':
            raise Exception("Unknown authentication method")
        auth = auth.strip().decode('base64')
        self.user, password = auth.split(':', 1)             
        if not database or not self.user or not password:
            raise Exception("Missing user, password or database")          
        if not req.session.authenticate(database, self.user, password):
            raise Exception("Odoo authentication failed")
        if language:
            # If not set we use the default language of the user
            req.session.context['lang'] = language
            

    @openerp.http.route('/frepple/xml',  type='http', auth='none', methods=['POST','GET'], csrf=False)
    def xml(self, **kwargs):
        database = kwargs.get('database', None)
        if not database:
            database = db_monodb()
        req = openerp.http.request
        language = kwargs.get('language', None)
        if req.httprequest.method == 'GET':
            # Login
            database = kwargs.get('database', None)  
            req.session.db = database                    
            try:                          
                self.authenticate(req, database, language)
            except Exception as e:
                logger.warning("Failed login attempt: %s" % e)  
                return Response(
                    'Login with Odoo user name and password', 401,
                    headers=[('WWW-Authenticate', 'Basic realm="odoo"')]
                    )
            
            # As an optional extra security check we can validate a web token attached
            # to the request. It allows use to verify that the request is generated
            # from frePPLe and not from somebody else.  
            
            # Generate data        
            try:
                xp = exporter(
                  req, 
                  database = database,
                  company = kwargs.get('company', None),
                  mode = int(kwargs.get('mode', 1))  
                  )
                # TODO Returning an iterator to stream the response back to the client and
                # to save memory on the server side
                return req.make_response(
                    ''.join([i for i in xp.run()]),
                    headers=[
                        ('Content-Type', 'application/xml;charset=utf8'),
                        ('Cache-Control', 'no-cache, no-store, must-revalidate'),
                        ('Pragma', 'no-cache'),
                        ('Expires', '0')
                    ])
            except Exception as e:
                logger.exception('Error generating frePPLe XML data')
                raise InternalServerError(description='Error generating frePPLe XML data: check the Odoo log file for more details')            
        elif req.httprequest.method == 'POST':
            # Authenticate the user
            database = req.httprequest.form.get('database', None)
            req.session.db = database
            try:                          
                self.authenticate(req, database, language)
            except Exception as e:
                logger.warning("Failed login attempt %s" % e)
                return Response(
                    'Login with Odoo user name and password', 401,
                    headers=[('WWW-Authenticate', 'Basic realm="odoo"')]
                    )

            # Validate the company argument
            company_name = req.httprequest.form.get('company', None)
            company = None                    
            m = req.session.model('res.company')
            m_search = m.search([('name', '=', company_name)])
            for i in m.browse(m_search):
              company = i
            if not company:
              return Response('Invalid company name argument', 401)
            
            # Verify that the data was posted from frePPLe and nobody else            
            try:
              webtoken = req.httprequest.form.get('webtoken', None)
              decoded = jwt.decode(
                webtoken,
                company.webtoken_key,
                algorithms=['HS256']
                )
              if self.user != decoded.get('user', None):
                return Response('Incorrect or missing webtoken', 401)
            except:
                return Response('Incorrect or missing webtoken', 401)
                            
            # Import the data    
            try:                 
                ip = importer(
                  req, 
                  database=database,
                  company=company,
                  mode=req.httprequest.form.get('mode', 1)
                  )
                return req.make_response(
                    ip.run(),
                    [
                        ('Content-Type', 'text/plain'),
                        ('Cache-Control', 'no-cache, no-store, must-revalidate'),
                        ('Pragma', 'no-cache'),
                        ('Expires', '0')
                    ])
            except Exception as e:
                logger.exception('Error processing data posted by frePPLe')
                raise InternalServerError(description='Error processing data posted by frePPLe: check the Odoo log file for more details')            
        else:
            raise MethodNotAllowed('Only GET and POST requests are accepted')
        