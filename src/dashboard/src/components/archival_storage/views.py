# This file is part of Archivematica.
#
# Copyright 2010-2013 Artefactual Systems Inc. <http://artefactual.com>
#
# Archivematica is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Archivematica is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Archivematica.  If not, see <http://www.gnu.org/licenses/>.

from django.shortcuts import render
from django.core.urlresolvers import reverse
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.utils import simplejson
from components.archival_storage import forms
from django.conf import settings
from main import models
from components import advanced_search
from components import helpers
import os
import sys
import slumber
import requests
sys.path.append("/usr/lib/archivematica/archivematicaCommon")
import elasticSearchFunctions
sys.path.append("/usr/lib/archivematica/archivematicaCommon/externals")
import pyes
import httplib
import tempfile
import subprocess
from components import decorators
from django.template import RequestContext

AIPSTOREPATH = '/var/archivematica/sharedDirectory/www/AIPsStore'

@decorators.elasticsearch_required()
def overview(request):
    return list_display(request)

@decorators.elasticsearch_required()
def page(request, page=None):
    return list_display(request, page)

def search(request):
    # deal with transfer mode
    file_mode = False
    checked_if_in_file_mode = ''
    if request.GET.get('mode', '') != '':
        file_mode = True
        checked_if_in_file_mode = 'checked'

    # get search parameters from request
    queries, ops, fields, types = advanced_search.search_parameter_prep(request)

    # redirect if no search params have been set
    if not 'query' in request.GET:
        return helpers.redirect_with_get_params(
            'components.archival_storage.views.search',
            query='',
            field='',
            type=''
        )

    # get string of URL parameters that should be passed along when paging
    search_params = advanced_search.extract_url_search_params_from_request(request)

    # set paging variables
    if not file_mode:
        items_per_page = 10
    else:
        items_per_page = 20

    page = advanced_search.extract_page_number_from_url(request)

    start = page * items_per_page + 1

    # perform search
    conn = pyes.ES(elasticSearchFunctions.getElasticsearchServerHostAndPort())

    try:
        query=advanced_search.assemble_query(queries, ops, fields, types)

        # use all results to pull transfer facets if not in file mode
        # pulling only one field (we don't need field data as we augment
        # the results using separate queries)
        if not file_mode:
            results = conn.search_raw(
                query=query,
                indices='aips',
                type='aipfile',
                fields='uuid'
            )
        else:
            results = conn.search_raw(
                query=query,
                indices='aips',
                type='aipfile',
                start=start - 1,
                size=items_per_page,
                fields='AIPUUID,filePath,FILEUUID'
            )
    except:
        return HttpResponse('Error accessing index.')

    # take note of facet data
    file_extension_usage = results['facets']['fileExtension']['terms']
    aip_uuids            = results['facets']['AIPUUID']['terms']

    if not file_mode:
        number_of_results = len(aip_uuids)

        page_data = helpers.pager(aip_uuids, items_per_page, page + 1)
        aip_uuids = page_data['objects']
        search_augment_aip_results(conn, aip_uuids)
    else:
        number_of_results = results.hits.total
        results = search_augment_file_results(results)

    # set remaining paging variables
    end, previous_page, next_page = advanced_search.paging_related_values_for_template_use(
       items_per_page,
       page,
       start,
       number_of_results
    )

    # make sure results is set
    try:
        if results:
            pass
    except:
        results = False

    form = forms.StorageSearchForm(initial={'query': queries[0]})
    return render(request, 'archival_storage/archival_storage_search.html', locals())

def search_augment_aip_results(conn, aips):
    for aip_uuid in aips:
        documents = conn.search_raw(query=pyes.FieldQuery(pyes.FieldParameter('uuid', aip_uuid.term)), fields='name,size,created')
        if len(documents['hits']['hits']) > 0:
            aip_uuid.name = documents['hits']['hits'][0]['fields']['name']
            aip_uuid.size = '{0:.2f} MB'.format(documents['hits']['hits'][0]['fields']['size'])
            aip_uuid.date = documents['hits']['hits'][0]['fields']['created']
            aip_uuid.document_id_no_hyphens = documents['hits']['hits'][0]['_id'].replace('-', '____')
        else:
            aip_uuid.name = '(data missing)' 

def search_augment_file_results(raw_results):
    modifiedResults = []

    for item in raw_results.hits.hits:
        clone = item.fields.copy()

        # try to find AIP details in database
        try:
            # get AIP data from ElasticSearch
            aip = elasticSearchFunctions.connect_and_get_aip_data(clone['AIPUUID'])

            # augment result data
            clone['sipname'] = aip.name
            clone['fileuuid'] = clone['FILEUUID']
            clone['href'] = aip.filePath.replace(AIPSTOREPATH + '/', "AIPsStore/")

        except:
            aip = None
            clone['sipname'] = False

        clone['filename'] = os.path.basename(clone['filePath'])
        clone['document_id'] = item['_id']
        clone['document_id_no_hyphens'] = item['_id'].replace('-', '____')

        modifiedResults.append(clone)

    return modifiedResults

def delete_context(request, uuid):
    prompt = 'Delete AIP?'
    cancel_url = reverse("components.archival_storage.views.overview")
    return RequestContext(request, {'action': 'Delete', 'prompt': prompt, 'cancel_url': cancel_url})

@decorators.confirm_required('archival_storage/delete_request.html', delete_context)
def aip_delete(request, uuid):
    reason_for_deletion = request.POST.get('reason_for_deletion', '')

    try:
        # send delete request
        api = slumber.API("http://localhost:8000/api/v1/")
        file_URI = "/api/v1/file/" + uuid + "/"
        api_request = {
            'event_reason': reason_for_deletion,
            'pipeline':     elasticSearchFunctions.getDashboardUUID(),
            'user_email':   request.user.email,
            'user_id':      request.user.id
        }
        response = api.file(file_URI).delete_aip.post(api_request)
        result = response['message']

        #elasticSearchFunctions.delete_aip(uuid)
        #elasticSearchFunctions.connect_and_delete_aip_files(uuid)
        #return HttpResponseRedirect(reverse('components.archival_storage.views.overview'))
    except requests.exceptions.ConnectionError:
        result = 'Unable to connect to storage server. Please contact your administrator.'
    except:
        raise Http404

    return render(request, 'archival_storage/delete_request_results.html', locals())

def aip_download(request, uuid):
    aip = elasticSearchFunctions.connect_and_get_aip_data(uuid)
    return helpers.send_file_or_return_error_response(request, aip.filePath, 'AIP')

def aip_file_download(request, uuid):
    # get file basename
    file          = models.File.objects.get(uuid=uuid)
    file_basename = os.path.basename(file.currentlocation)

    # get file's AIP's properties
    sipuuid      = helpers.get_file_sip_uuid(uuid)
    aip          = elasticSearchFunctions.connect_and_get_aip_data(sipuuid)
    aip_filepath = aip.filePath

    # create temp dir to extract to
    temp_dir = tempfile.mkdtemp()

    # work out path components
    aip_archive_filename = os.path.basename(aip_filepath)
    subdir = os.path.splitext(aip_archive_filename)[0]
    path_to_file_within_aip_data_dir \
      = os.path.dirname(file.originallocation.replace('%transferDirectory%', ''))

    file_relative_path = os.path.join(
      subdir,
      'data',
      path_to_file_within_aip_data_dir,
      file_basename
    )

    #return HttpResponse('7za e -o' + temp_dir + ' ' + aip_filepath + ' ' + file_relative_path)

    # extract file from AIP
    command_data = [
        '7za',
        'e',
        '-o' + temp_dir,
        aip_filepath,
        file_relative_path
    ]

    subprocess.call(command_data)

    # send extracted file
    extracted_file_path = os.path.join(temp_dir, file_basename)
    return helpers.send_file(request, extracted_file_path)

def send_thumbnail(request, fileuuid):
    # get AIP location to use to find root of AIP storage
    sipuuid = helpers.get_file_sip_uuid(fileuuid)
    aip = elasticSearchFunctions.connect_and_get_aip_data(sipuuid)
    aip_filepath = aip.filePath

    # strip path to AIP from root of AIP storage
    for index in range(1, 10):
        aip_filepath = os.path.dirname(aip_filepath)

    # derive thumbnail path
    thumbnail_path = os.path.join(
        aip_filepath,
        'thumbnails',
        sipuuid,
        fileuuid + '.jpg'
    )

    # send "blank" thumbnail if one exists:
    # Because thumbnails aren't kept in ElasticSearch they can be queried for,
    # during searches, from multiple dashboard servers.
    # Because ElasticSearch don't know if a thumbnail exists or not, this is
    # a way of not causing visual disruption if a thumbnail doesn't exist.
    if not os.path.exists(thumbnail_path):
        thumbnail_path = os.path.join(settings.BASE_PATH, 'media/images/1x1-pixel.png')

    return helpers.send_file(request, thumbnail_path)

def get_file_status(uuid):
   api = slumber.API("http://localhost:8000/api/v1/")
   file_URI = "/api/v1/file/" + uuid + "/"
   response = api.file(file_URI).get()

   return response['status']

def aip_file_count():
    # get UUIDs of AIPs where deletion has been requested
    api = slumber.API("http://localhost:8000/api/v1/")
    files_URI = "/api/v1/file/"
    response = api.file(files_URI).get(status='DEL_REQ')

    # add these UUIDs to exclusion when counting AIP files
    must_not_haves = []
    for aip in response['objects']:
        must_not_haves.append(pyes.TermQuery('AIPUUID', aip['uuid']))
    query = pyes.BoolQuery(must_not=must_not_haves)

    return advanced_search.indexed_count('aips', ['aipfile'], query)

def list_display(request, current_page_number=None):
    form = forms.StorageSearchForm()

    # get count of AIP files
    aip_indexed_file_count = aip_file_count()

    # get AIPs
    order_by = request.GET.get('order_by', 'name')
    sort_by  = request.GET.get('sort_by', 'up')

    if sort_by == 'down':
        sort_direction = 'desc'
    else:
        sort_direction = 'asc'

    sort_specification = order_by + ':' + sort_direction

    conn = elasticSearchFunctions.connect_and_create_index('aips')
    aipResults = conn.search(
        pyes.MatchAllQuery(),
        doc_types=['aip'],
        fields='origin,uuid,filePath,created,name,size',
        sort=sort_specification
    )

    aips = []

    if len(aipResults) > 0:
        for aip in aipResults:
            aip['status'] = get_file_status(aip['uuid'])
            if aip['status'] != 'DEL_REQ':
                aips.append(aip)

    # handle pagination
    page = helpers.pager(aips, 10, current_page_number)

    sips = []
    for aip in page['objects']:
        sip = {}
        sip['href']   = aip.filePath.replace(AIPSTOREPATH + '/', "AIPsStore/")
        sip['name']   = aip.name
        sip['uuid']   = aip.uuid
        sip['status'] = aip.status
        sip['date']   = aip.created

        try:
            size = float(aip.size)
            sip['size'] = '{0:.2f} MB'.format(size)
        except:
            sip['size'] = 'Removed'

        sips.append(sip)

    # get total size of all AIPS from ElasticSearch
    q = pyes.MatchAllQuery().search()
    q.facet.add(pyes.facets.StatisticalFacet('total', field='size'))
    aipResults = conn.search(q, doc_types=['aip'])
    total_size = aipResults.facets.total.total
    total_size = '{0:.2f}'.format(total_size)

    return render(request, 'archival_storage/archival_storage.html', locals())

def document_json_response(document_id_modified, type):
    document_id = document_id_modified.replace('____', '-')
    conn = httplib.HTTPConnection(elasticSearchFunctions.getElasticsearchServerHostAndPort())
    conn.request("GET", "/aips/" + type + "/" + document_id)
    response = conn.getresponse()
    data = response.read()
    pretty_json = simplejson.dumps(simplejson.loads(data), sort_keys=True, indent=2)
    return HttpResponse(pretty_json, content_type='application/json')

def file_json(request, document_id_modified):
    return document_json_response(document_id_modified, 'aipfile')

def aip_json(request, document_id_modified):
    return document_json_response(document_id_modified, 'aip')
