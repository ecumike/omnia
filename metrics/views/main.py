import io
import json
import requests
import sys
import traceback
import pandas as pd

from datetime import datetime, timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count, Value, Sum, Q, Avg, F
from django.db.models.functions import Lower
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.debug import ExceptionReporter


from django.conf import settings
from ..models import *
from research.models import Artifact
from info.models import WhatsNew
import metrics.helpers as helpers
import metrics.access_helpers as accessHelpers
from metrics.forms import *



def getBreadcrumbBase():
	return [
		{
			'text': 'Metrics',
			'url': reverse('metrics:home')
		}
	]
	
############################################################
#### Redirects
############################################################

##	/metrics/projectdetail/?project=<ID>
##
## Redirect from old detail URL
##
def redirect_project_detail(request):
	url = f"{reverse('metrics:projects_detail')}?project={request.GET.get('project', None)}"
	return redirect(url)


##	/metrics/responsescomments/? <params>
##
## Redirect from old responses comments URL
##
def redirect_project_comments(request):
	url = f"{reverse('metrics:projects_vote_responses')}?{request.GET.urlencode()}"
	return redirect(url)


############################################################


##
##	/feedback.js
##
##	Feedback js. Has to load from same server, can't do static on CDN.
##
##
# def feedback_js(request):
# 	response = render(request, 'feedback.js', {})
# 	return HttpResponse(response, content_type='text/javascript')


##
##	/metrics/
##
##	Only routes based on: Project|Domain + Tile|Table
##  Filters for tiles or table view goes here and we route to the proper URL/view.
##  Much easier than putting logic in a bunch of views.
##
##
def metrics_home(request):
	# Get the specified domain, or one from session last used.
	# Current local speed avg: 135q in 181ms
	# getHistoricalNpsCatCountChartData is the bottleneck.
	selectedDomain = helpers.getFilterDomain(request)
	
	try:
		domain = Domain.objects.get(id=selectedDomain)
	except:
		domain = None
	
	try:
		keyword = ProjectKeyword.objects.get(id=request.GET.get('keyword', None))
	except:
		keyword = None
	
	# Get domain's core projects currently reporting snapshots.
	domainCoreProjectSnapshots = ProjectSnapshot.getCoreCurrentlyReportingProjectSnapshots(domain=domain, keyword=keyword).select_related('project', 'project__domain')

	# Get the domain-level metrics. Either all domains, or the one specified.
	# It's not the true snapshot obj, it's a manual created one with same fields so we can use for "all" domains.
	# This is shared on all 3 domain metrics pages.
	domainSnapshot = Domain.getCombinedMetrics(domain=domain, keyword=keyword)
	
	# Domain-level pie chart of three metrics.
	domainSnapshot['npsScoreCategories'] = NpsScoreCategory.getCategoryCounts(domainCoreProjectSnapshots, includeZeros=False)
	domainSnapshot['umuxScoreCategories'] = UmuxScoreCategory.getCategoryCounts(domainCoreProjectSnapshots, includeZeros=False)
	domainSnapshot['goalCompletedCategories'] = GoalCompletedCategory.getCategoryCounts(domainCoreProjectSnapshots, includeZeros=False)
	
	# Domain-level lists: Best/worst/improved.
	mostImprovedProjects = domainCoreProjectSnapshots.filter(project__project_year_setting_project__year=timezone.now().year, project__project_year_setting_project__nps_baseline__isnull=False).only('project__name', 'project__domain').annotate(diff=F('nps_score')-F('project__project_year_setting_project__nps_baseline')).order_by('-diff')[:5]
	#mostImprovedProjects = domainCoreProjectSnapshots.filter(project__project_year_setting_project__year=timezone.now().year, project__project_year_setting_project__nps_baseline__isnull=False).only('project__name', 'project__domain').annotate(diff=(((F('nps_score')-F('project__project_year_setting_project__nps_baseline'))/200)*100)).order_by('-diff')[:5]
	bestCompletionProjects = domainCoreProjectSnapshots.filter(goal_completed_percent__isnull=False).only('project__name', 'project__domain', 'goal_completed_percent').order_by('-goal_completed_percent')[:5]
	
	## "All domains" bottom row metrics data here.
	
	# These are NOT "core project" scoped.
	allProjects = Project.objects.allActive().only('id')
	currentlyReportingProjects = allProjects.exclude(latest_valid_currently_reporting_snapshot__isnull=True)
	currentlyReportingExcellentCount = currentlyReportingProjects.filter(latest_valid_currently_reporting_snapshot__nps_meaningful_data=True, latest_valid_currently_reporting_snapshot__nps_score_category__name='Excellent').count()
	
	try:
		currentlyReportingExcellentPercent = (currentlyReportingExcellentCount/currentlyReportingProjects.count())*100
	except:
		currentlyReportingExcellentPercent = 0
	
	# For each quarter in this range (since inception)
	# Sum the NPS/UMUX/GC counts and add object to data array for the line chart to use.
	responsesHistoryChartData = VoteResponse.getVoteResponsesCountsHistory(
		projects = allProjects,
		startDate = timezone.datetime(2017, 5, 1), #VoteResponse.objects.order_by('date').values('date').first()['date'],
		endDate = timezone.now(), #VoteResponse.objects.order_by('date').values('date').last()['date'],
	)
	
	context = {
		'mostImprovedProjects': mostImprovedProjects,
		'worstNpsProjects': domainCoreProjectSnapshots.order_by('nps_score').select_related('nps_score_category')[:5],
		#'bestNpsProjects': domainCoreProjectSnapshots.order_by('-nps_score')[:5],
		'bestCompletionProjects': bestCompletionProjects,
		'selectedDomain': domain,
		'selectedKeyword': keyword,
		'domains': Domain.objects.exclude(project_domain__isnull=True).only('name'),
		'domainSnapshot': domainSnapshot,
		'allDomains': {
			'allProjectsCount': allProjects.count(),
			'currentlyReportingExcellentCount': currentlyReportingExcellentCount,
			'currentlyReportingCount': currentlyReportingProjects.count(),
			'currentlyReportingExcellentPercent': currentlyReportingExcellentPercent,
			'responsesHistoryChartData': responsesHistoryChartData,
			'npsHistoryChartData': ProjectSnapshot.getHistoricalNpsCatCountChartData(),
		},
		'researchItems': Artifact.objects.exclude(archived=True).only('sort_date', 'name', 'abstract').order_by('-sort_date')[:3],
		'whatsnewItems': WhatsNew.objects.all().only('date', 'heading', 'description')[:3],
		'menunavItem': 'dashboard',
		'projectKeywords': ProjectKeyword.objects.filter(project_keywords__isnull=False).distinct(),
		'linkFilter': f'keyword={keyword.id}' if keyword else 'priority=1&priority=2&priority=3',
		'headerItem': 'metrics',
	}
	
	request.session['filterDomain'] = selectedDomain
	
	return render(request, 'metrics/home.html', context)


##
##	/metrics/tilestableview/
##
##	Only routes based on: Project|Domain + Tile|Table
##  Filters for tiles or table view goes here and we route to the proper URL/view.
##  Much easier than putting logic in a bunch of views.
##
##
def tiles_table_router(request):
	displayDomainsAs = helpers.getDomainsDisplay(request)
	#displayProjectsAs = helpers.getProjectsDisplay(request)
	returnUrl = None
	
	if 'projects' in request.path_info:
		returnUrl = reverse('metrics:projects_home')
	else:
		if displayDomainsAs == 'table':
			returnUrl = reverse('metrics:domains_table')
		else:
			returnUrl = reverse('metrics:domains_tiles')
	
	return redirect(f'{returnUrl}?{request.GET.urlencode()}')


##
##	/metrics/domains/
##
##	Tile view of domains.
##
##
def domains_tiles(request):
	context = {
		'domainSnapshots': DomainYearSnapshot.objects.filter(year=timezone.now().year).annotate(
			domainId=F('domain_id'), 
			domainName=F('domain__name'), 
			leadName=F('domain__lead__profile__full_name'),
			leadEmail=F('domain__lead__username'),
		),
		'allDomainsMetrics': Domain.getCombinedMetrics(domain=None),
		'menunavItem': 'domains',
		'jumplinksProjects': Project.objects.allActive(),
	}
	
	request.session['displayDomainsAs'] = 'tiles'
	response = render(request, 'metrics/domains_tiles.html', context)
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/domains/table/
##
##	# Hidden, no longer linked.
##
##
def domains_table(request):
	def getDomainAggregate(field):
		return DomainYearSnapshot.objects.filter(year=timezone.now().year).aggregate(Sum(field))[field + '__sum']
	
	npsScoreCategories = NpsScoreCategory.objects.order_by('-min_score_range')
	domainSnapshots = DomainYearSnapshot.objects.filter(year=timezone.now().year).select_related('domain')
	allDomainsMetrics = Domain.getCombinedMetrics(domain=None)
	
	# Preset array with placeholder counters we can just add to.
	totalNpsCategories = []
	for category in npsScoreCategories:
		totalNpsCategories.append({
			'colorCode': category.color_code,
			'numProjects': 0
		})
	
	# For each domain
	for domainSnapshot in domainSnapshots:
		domainProjects = Project.objects.allActive().filter(domain=domainSnapshot.domain, core_project=True).select_related('currently_reporting_snapshot__nps_score_category')
		
		# For each NPS category:
		# Add the count of projects in the category to the domain row and overall "total" row.
		domainSnapshot.npsScoreCategories = []
		for i, category in enumerate(npsScoreCategories, start=0):
			num = domainProjects.filter(currently_reporting_snapshot__nps_score_category=category, currently_reporting_snapshot__nps_meaningful_data=True).values('id').distinct().count()
			
			if num > 0:
				totalNpsCategories[i]['numProjects'] += num
			else:
				num = ''
			
			domainSnapshot.npsScoreCategories.append(num)
			
	allDomainsMetrics['npsCategories'] = totalNpsCategories

	context = {
		'domainSnapshots': domainSnapshots,
		'allDomainsMetrics': allDomainsMetrics,
		'npsScoreCategories': npsScoreCategories,
		'menunavItem': 'domains',
	}

	request.session['displayDomainsAs'] = 'table'
	response = render(request, 'metrics/domains_table.html', context)
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/projects/
##
##	Project home page: tile list.
##
##
def projects_home(request):
	'''
	Stats for view (avgs) (prod is faster than local):
		Show all (default): 245, 123q/87ms
		Achieving target: 89 of 245, 21q/52ms
		Excellent NPS: 55 of 245, 20q/35ms
	'''
	
	projectsWithoutSnapshot = None
	tileFiltersData = helpers.createProjectTilesFiltersData(request)
	tileFiltersData['domains'] = Domain.objects.exclude(project_domain__isnull=True).distinct().only('name')
	tileFiltersData['npsCategories'] = NpsScoreCategory.objects.only('name')
	tileFiltersData['umuxCategories'] = UmuxScoreCategory.objects.only('name')
	tileFiltersData['goalCategories'] = GoalCompletedCategory.objects.only('name')
	tileFiltersData['tasks'] = Task.objects.filter(parent_task__isnull=True).only('name')
	
	# Legacy value switch to latest NPS
	if tileFiltersData['selectedShowData'] == 'health_score':
		tileFiltersData['selectedShowData'] = 'nps_score'
		tileFiltersData['selectedReportPeriod'] = 'last90'

	# Get PROJECTS using project filters: domain, priority, keywords.
	# We then get a filtered set from that, and ones that don't have snapshot get "nothing for period".
	projects = Project.getFilteredSet(tileFiltersData, None).select_related('domain', 'latest_valid_currently_reporting_snapshot', 'latest_valid_currently_reporting_snapshot__nps_score_category', 'latest_valid_currently_reporting_snapshot__umux_score_category', 'latest_valid_currently_reporting_snapshot__goal_completed_category').prefetch_related('task_projects')
	
	# Get SNAPSHOTS for the projects using the report period and all the optional checkbox filters.
	# Ones with no snapshots get listed as "nothing for period"
	try:
		projectSnapshots = ProjectSnapshot.getFilteredSet(tileFiltersData, projects)
		
		# If they filtered on any of the categories or scores filters, then we have to further filter the projects
		#  and only include those that have a snapshot. 
		# Use of these filters will never show "not enough/no data" tiles.
		if tileFiltersData['selectedNpsCats'] or tileFiltersData['selectedUmuxCats'] or tileFiltersData['selectedGoalCats'] or tileFiltersData['selectedMeetingNpsTarget'] == 'y' or tileFiltersData['selectedMeetingUmuxTarget'] == 'y' or tileFiltersData['selectedExceedingNpsTarget'] == 'y' or tileFiltersData['selectedExceedingUmuxTarget'] == 'y':
			projects = Project.objects.filter(project_snapshot_project__in=projectSnapshots)
		
		projectsWithScoresArr = projectSnapshots.values_list('project', flat=True)
		projectsWithoutSnapshot = projects.exclude(id__in=projectsWithScoresArr)
		
	except Exception as ex:
		print(ex)
		projectSnapshots = None
		projectsWithoutSnapshot = None
		
	try:
		resultsCount = (projectSnapshots.count() if projectSnapshots else 0) + (projectsWithoutSnapshot.count() if projectsWithoutSnapshot else 0)
	except:
		resultsCount = 0
		
		
	context = {
		'totalProjectsCount': Project.objects.allActive().count(),
		'resultsCount': resultsCount,
		'projectSnapshots': projectSnapshots,
		'projectsWithoutSnapshot': projectsWithoutSnapshot,
		'tileFiltersData': tileFiltersData,
		'thisYear': timezone.now().year,
		'legendModalNpsScoreCategories': NpsScoreCategory.objects.all(),
		'legendModalUmuxScoreCategories': UmuxScoreCategory.objects.all(),
		'legendModalGoalCompletedCategories': GoalCompletedCategory.objects.all(),
		'priorities': [1,2,3,4,5],
		'projectKeywords': ProjectKeyword.objects.filter(project_keywords__isnull=False).distinct(),
		'menunavItem': 'projects',
	}
	
	response = render(request, 'metrics/projects_tiles.html', context)
	
	request.session['filterDomain'] = tileFiltersData['selectedDomain']
	request.session['filterShowData'] = tileFiltersData['selectedShowData']
	request.session['filterReportPeriod'] = tileFiltersData['selectedReportPeriod']
	
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/projects/detail/?<project>
##
##	Project detail template.
##
##
def projects_detail(request):
	try:
		# Use filter for prefetching, and then [0] to force error/404 on non valid project.
		project = Project.objects.filter(id=request.GET.get('project')).select_related('domain', 'current_year_settings', 'contact', 'contact__profile').prefetch_related('admins', 'editors', 'admins__profile', 'editors__profile', 'task_projects')[0]
	except Exception as ex:
		print(ex)
		return render(request, '404.html', {}, status=404)

	# Get proper report period, snapshot, and responses based on the project and report period selected.
	reportPeriodData = project.getReportPeriodData(request)
	selectedReportPeriod = reportPeriodData['reportPeriod']
	projectSnapshot = reportPeriodData['projectSnapshot']
	projectSnapshotResponses = reportPeriodData['projectSnapshotResponses']
	
	if reportPeriodData['timeMachineStartDate']:
		selectedReportPeriod += f"&startdate={reportPeriodData['timeMachineStartDate'].strftime('%Y-%m-%d')}"
		
	## NOTE: From here it doesn't matter what the snapshot or responses are for the project.
	# Everything below here runs on: project, snapshot, responses.

	# Get all project snapshots except secret monthly ones (dupes of quarters).
	# Then generate chart data for all the charts.
	allProjectSnapshots = project.project_snapshot_project.exclude(date_period='month')	
	
	# Get list of report periods this project has snapshots for.
	# If they are using the secret month snapshot, set report period to none.
	reportPeriodChoices = []
	for ps in allProjectSnapshots.filter(date_period='quarter').exclude(date_quarter=None):
		reportPeriodChoices.append( (f'{ps.date_quarter}q{ps.date.year}', f'{ps.date_quarter}Q{ps.date.year}') )
	
	if allProjectSnapshots.filter(date_period='last90').exists():
		reportPeriodChoices.insert(0, ('last90','Current score'))
	
	reportPeriodChoices.insert(0, ('NA','----'))
	
	try:
		responsesPerDay = round(projectSnapshot.nps_count / projectSnapshot.response_day_range, 1)
	except Exception as ex:
		responsesPerDay = 0
	
	# If there's a snapshot.
	if projectSnapshot:
		projectSnapshot.comments = projectSnapshotResponses.exclude(comments='').order_by('-date')[:10].values_list('comments', flat=True)
		projectSnapshot.suggestions = projectSnapshotResponses.exclude(improvement_suggestion='').order_by('-date')[:10].values_list('improvement_suggestion', flat=True)
		projectSnapshot.goal_not_completeds = projectSnapshotResponses.exclude(goal_not_completed_reason='').order_by('-date')[:10].values_list('goal_not_completed_reason', flat=True)
		
		if projectSnapshot.nps_margin_error and projectSnapshot.nps_margin_error >= 13 and projectSnapshot.nps_margin_error <= 17:
			projectSnapshot.npsMoeWarning = 'It is recommended that you increase your sample size to try and reduce your margin of error to below 13'
		
		if projectSnapshot.umux_margin_error and projectSnapshot.umux_margin_error >= 5 and projectSnapshot.umux_margin_error <= 7:
			projectSnapshot.umuxMoeWarning = 'It is recommended that you increase your sample size to try and reduce your margin of error to below 5'
	
	if allProjectSnapshots.count() > 0:
		hasAnyData = True
	else:
		hasAnyData = False

	# If we're using time machine, limit the historical chart to snapshots before the reportperiod date.
	if reportPeriodData['usingTimeMachine']:
		historyChartSnapshots = allProjectSnapshots.filter(date__lte=reportPeriodData['timeMachineEndDate'])
	else:
		historyChartSnapshots = allProjectSnapshots
		
	breadcrumbs = [
		{
			'text': 'Tools & services',
			'url': reverse('metrics:projects_home')
		},
	]
	
	context = {
		'breadcrumbs': breadcrumbs,
		'project': project,
		'projectResearchCount': project.artifact_projects.count(),
		'projectEvents': project.project_event_project.all(),
		'projects': Project.objects.allActive().exclude(project_snapshot_project__isnull=True).order_by(Lower('name')).only('name'),
		'selectedReportPeriod': selectedReportPeriod,
		'reportPeriodChoices': reportPeriodChoices,
		'projectSnapshot': projectSnapshot,
		'isProjectEditor': accessHelpers.isProjectEditor(request.user, project),
		'isProjectAdmin': accessHelpers.isProjectAdmin(request.user, project),
		'npsChartDataArr': helpers.getHistoricalNpsChartData(historyChartSnapshots),
		'npsHistogramData': helpers.getNpsHistogramData(projectSnapshotResponses),
		'npsScoreCategories': NpsScoreCategory.objects.order_by('-max_score_range').all(),
		'umuxChartDataArr': helpers.getHistoricalUmuxChartData(historyChartSnapshots),
		'umuxCapHistogramData': helpers.getUmuxCapHistogramData(projectSnapshotResponses),
		'umuxEaseHistogramData': helpers.getUmuxEaseHistogramData(projectSnapshotResponses),
		'umuxScoreCategories': UmuxScoreCategory.objects.order_by('max_score_range').all(),
		'goalCompletionChartDataArr': helpers.getHistoricalGoalCompletionChartData(historyChartSnapshots),
		'goalHistogramData': helpers.getGoalHistogramData(projectSnapshotResponses),
		'responsesPerDay': responsesPerDay,
		'hasAnyData': hasAnyData,
		'legendModalNpsScoreCategories': NpsScoreCategory.objects.all(),
		'legendModalUmuxScoreCategories': UmuxScoreCategory.objects.all(),
		'legendModalGoalCompletedCategories': GoalCompletedCategory.objects.all(),
		'customTimeMachineMessage': reportPeriodData['customTimeMachineMessage'],
		'feedbackResponses': project.getFeedbackResponses().order_by('-date')[:10],
		'menunavItem': 'projects',
	}
	
	if reportPeriodData['usingTimeMachine'] or 'm' in selectedReportPeriod:
		selectedReportPeriod = 'last90'

	response = render(request, 'metrics/projects_detail.html', context)
	request.session['filterReportPeriod'] = selectedReportPeriod
	helpers.clearPageMessage(request)
	
	# Delete the temp snapshot from the DB.
	#projectSnapshot.delete()
	
	return response


##
##	/metrics/projects/responses/
##
##	Responses comments viewer. Filter on project and what field to show.
##
##
def projects_vote_responses(request):
	try:
		project = get_object_or_404(Project, id=request.GET.get('project'))
	except Exception as ex:
		return render(request, '404.html', {}, status=404)
		
	allowedFields = {
		'comments': 'Comments',
		'goal_not_completed_reason': 'Reason goal not completed',
		'improvement_suggestion': 'Suggestion for improvement'
	}
	
	fieldToShow = request.GET.get('showdata', 'comments')
	try:
		fieldToShowLabel = allowedFields[fieldToShow]
	except Exception as ex:
		fieldToShow = 'comments'
		fieldToShowLabel = allowedFields[fieldToShow]
	
	# Get proper report period, snapshot, and responses based on the project and report period selected.
	reportPeriodData = project.getReportPeriodData(request)
	selectedReportPeriod = reportPeriodData['reportPeriod']
	projectSnapshot = reportPeriodData['projectSnapshot']
	projectSnapshotResponses = reportPeriodData['projectSnapshotResponses']
	
	if reportPeriodData['timeMachineStartDate']:
		selectedReportPeriod += f"&startdate={reportPeriodData['timeMachineStartDate'].strftime('%Y-%m-%d')}"
	
	# Exclude responses that don't have a comment for the field we want to show.
	excludeArgs = {
		f'{fieldToShow}': ''
	}
	# Special Kyndryl project.
	if project.id == 611:
		excludeArgs = {
			'comments': '',
			'improvement_suggestion': '',
		}
	try:
		responses = projectSnapshotResponses.exclude(**excludeArgs).select_related('primary_goal')
		emptyResponseCount = projectSnapshotResponses.filter(**excludeArgs).count()	
	except Exception as ex:
		responses = None
		emptyResponseCount = 0
	
	## NOTE: From here it doesn't matter what the snapshot or responses are for the project.
	# Everything below here runs on: project, snapshot, responses.

	# Get all project snapshots except secret monthly ones (dupes of quarters).
	# Then generate chart data for all the charts.
	allProjectSnapshots = project.project_snapshot_project.exclude(date_period='month')	

	# Get list of periods this project has snapshots for.
	# We need to filter out monthly	snapshots now so they don't get included in time period selector
	reportPeriodChoices = []
	for ps in allProjectSnapshots.filter(date_period='quarter').exclude(date_quarter=None):
		reportPeriodChoices.append( (f'{ps.date_quarter}q{ps.date.year}', f'{ps.date_quarter}Q{ps.date.year}') )
	
	if allProjectSnapshots.filter(date_period='last90').exists():
		reportPeriodChoices.insert(0, ('last90','Current score'))
	
	reportPeriodChoices.insert(0, ('','----'))			
	
	showFieldOptions = []
	for item in allowedFields.items():
		showFieldOptions.append((item[0], item[1]))
	
	breadcrumbs = [
		{
			'text': 'Tools &amp; services',
			'url': reverse('metrics:projects_home')
		},
		{ 
			'text': project.name,
			'url': f"{reverse('metrics:projects_detail')}?project={project.id}&reportperiod={selectedReportPeriod}"
		},
	]

	context = {
		'breadcrumbs': breadcrumbs,
		'isProjectAdmin': accessHelpers.isProjectAdmin(request.user, project),
		'responses': responses,
		'emptyResponseCount': emptyResponseCount,
		'projects': Project.objects.allActive().all(),
		'selectedProject': project,
		'showFieldOptions': showFieldOptions,
		'selectedField': fieldToShow,
		'selectedFieldLabel': allowedFields[fieldToShow],
		'selectedReportPeriod': selectedReportPeriod,
		'reportPeriodChoices': reportPeriodChoices,
		'customTimeMachineMessage': reportPeriodData['customTimeMachineMessage'],
		'menunavItem': 'projects',
	}
		
	response = render(request, 'metrics/projects_vote_responses.html', context)
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/projects/feedback/
##
##	Feedback Responses viewer. 
##
def projects_feedback_responses(request):
	try:
		project = get_object_or_404(Project, id=request.GET.get('project'))
	except Exception as ex:
		return render(request, '404.html', {}, status=404)
	
	isProjectEditor = accessHelpers.isProjectEditor(request.user, project)
	
	if not isProjectEditor:
		return render(request, '403.html', {}, status=403)	
		
	# Get proper report period, snapshot, and responses based on the project and report period selected.
	allResponses = project.getFeedbackResponses()
	
	# Exclude responses that don't have a comment for the field we want to show.
	excludeArgs = {'comments': ''}
	try:
		responses = allResponses
		emptyResponseCount = 0
	except Exception as ex:
		responses = None
		emptyResponseCount = 0
	
	breadcrumbs = [
		{
			'text': 'Tools &amp; services',
			'url': reverse('metrics:projects_home')
		},
		{ 
			'text': project.name,
			'url': f"{reverse('metrics:projects_detail')}?project={project.id}"
		},
	]
	
	context = {
		'breadcrumbs': breadcrumbs,
		'isProjectEditor': accessHelpers.isProjectEditor(request.user, project),
		'responses': responses,
		'emptyResponseCount': emptyResponseCount,
		'projects': Project.objects.allActive().filter(campaign_project__feedback_response_campaign__isnull=False).distinct(),
		'selectedProject': project,
		'menunavItem': 'projects',
	}
		
	response = render(request, 'metrics/projects_feedback_responses.html', context)
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/claudeistheman/
##
def claude_temp(request):
	response = render(request, 'metrics/claude_temp.html', {})
	return response


##
##	/metrics/claudeistheman/
##
def claude_temp_dabby1(request):
	response = render(request, 'metrics/claude_temp_dabby1.html', {})
	return response


##
##	/metrics/claudeistheman/
##
def claude_temp_dabby2(request):
	response = render(request, 'metrics/claude_temp_dabby2.html', {})
	return response


##
##	/metrics/alerts/
##
##	List of logged alerts for campaigns, projects and domains.
##
##
def alerts(request):
	alerts = Alert.objects.all().select_related('project')
	selectedProjectIds = [int(i) for i in request.GET.getlist('project', None)]
	selectedProjects = Project.objects.filter(id__in=selectedProjectIds)
	selectedTypes = request.GET.getlist('type')
	
	if selectedProjectIds:
		alerts = alerts.filter(project__id__in=selectedProjectIds)
	
	if selectedTypes:
		alerts = alerts.filter(type__in=selectedTypes)
	
	displayMsg = ''
	if not alerts:
		displayMsg = 'No alerts found with the selected filters.'
	if len(selectedTypes) == 1 and 'Poop' in selectedTypes:
		displayMsg = 'Oops there\'s no poop.'
		
	context = {
		'breadcrumbs': getBreadcrumbBase(),
		'alerts': alerts,
		'types': ['Info', 'Great', 'Good', 'Warning', 'Bad', 'Poop'],
		'projects': Project.objects.filter(alert_project__isnull=False).order_by(Lower('name')).distinct().annotate(numAlerts=Count('alert_project')).only('name'),
		'selectedProjectIds': selectedProjectIds,
		'selectedProjects': selectedProjects,
		'selectedTypes': selectedTypes,
		'displayMsg': displayMsg
	}
		
	response = render(request, 'metrics/alerts.html', context)
	helpers.clearPageMessage(request)
	return response


##
##	/metrics/alerts/
##
##	List of logged alerts for campaigns, projects and domains.
##
##
def response_counts(request):
	monthNames = []
	monthNums = list(range(1, timezone.now().month + 1))
	year = timezone.now().year
	
	for mNum in monthNums:
		monthNames.append(timezone.datetime(1900, mNum, 1).strftime('%B'))
	
	projects = Project.objects.allActive().order_by(Lower('name')).select_related('domain', 'domain__lead__profile')
	for project in projects:
		snapshots = ProjectSnapshot.objects.filter(
			project=project, 
			date_period='month', 
			date__year=year, 
			date__month__in=monthNums).order_by('date__month').only('date', 'meaningful_response_count')
		
		project.monthCounts = []
		hasMonths = []
		for snapshot in snapshots:
			project.monthCounts.append(snapshot.meaningful_response_count)
			hasMonths.append(snapshot.date.month)
			
		missingMonths = list(set(monthNums) - set(hasMonths))
		for m in missingMonths:
			project.monthCounts.insert(m-1, 0)
	
	domains = Domain.objects.all().order_by(Lower('name')).select_related('lead__profile')
	for d in domains:
		countBymonth = []
		for mNum in monthNums:
			respCount = d.project_domain.filter(
				project_snapshot_project__date__year=year,
				project_snapshot_project__date__month=mNum, 
				project_snapshot_project__date_period='month').aggregate(
					npsCount=Sum('project_snapshot_project__meaningful_response_count')
				)['npsCount']
			if respCount:
				countBymonth.append(respCount)
			else:
				countBymonth.append(0)
		d.monthCounts = countBymonth
		
	context = {
		'breadcrumbs': getBreadcrumbBase(),
		'projects': projects,
		'domains': domains,
		'monthNames': monthNames,
	}
	
	response = render(request, 'metrics/response_counts.html', context)
	return response


##
##	/metrics/tasks/
##
def tasks_home(request):
	tasks = Task.objects.filter(task_parent_task__isnull=False).order_by('-ease', 'name').select_related('role', 'score_category', 'parent_task').annotate(numSubtasks=Count('task_parent_task'))
	
	selectedScore = request.GET.get('score', None)
	try:
		scoreCat = UmuxScoreCategory.objects.get(id=selectedScore)
		tasks = tasks.filter(score_category=scoreCat)
	except:
		scoreCat = None
		
	selectedRole = request.GET.get('role', None)
	try:
		roleObj = Role.objects.get(id=selectedRole)
		tasks = tasks.filter(role=roleObj)
	except:
		pass
		
	context = {
		'tasks': tasks,
		'scores': UmuxScoreCategory.objects.filter(task_score_category__isnull=False).distinct(),
		'roles': Role.objects.filter(task_role__isnull=False).distinct(),
		'selectedScore': selectedScore,
		'selectedRole': selectedRole,
		'menunavItem': 'tasks',
	}
	
	response = render(request, 'metrics/tasks_list.html', context)
	return response


##
##	/metrics/tasks/id/
##
def tasks_detail(request, id):
	try:
		task = Task.objects.filter(id=id).select_related('role', 'score_category').prefetch_related('projects', 'task_parent_task')[0]
		if not task:
			return render(request, '404.html', {}, status=404)
	except:
		return render(request, '404.html', {}, status=404)
	
	goalCategory = GoalCompletedCategory.getCategory(task.score)
	
	
	tileFiltersData = {}
	tileFiltersData['selectedShowData'] = 'nps_score'
	tileFiltersData['selectedReportPeriod'] = 'last90'

	# Get PROJECTS using project filters: domain, priority, keywords.
	# We then get a filtered set from that, and ones that don't have snapshot get "nothing for period".
	projects = task.projects.select_related('domain', 'latest_valid_currently_reporting_snapshot', 'latest_valid_currently_reporting_snapshot__nps_score_category')
	
	# Get SNAPSHOTS for this tasks projects.
	try:
		snapshotArgsLast90 = {
			'project_currently_reporting_snapshot__isnull': False,
			'nps_score__isnull': False,
			'project__in': projects
		}
		
		try:
			projectSnapshots = ProjectSnapshot.objects.filter(**snapshotArgsLast90).order_by(Lower('project__name')).select_related('project', 'project__current_year_settings', 'nps_score_category')
		except:
			projectSnapshots = None
			
		projectsWithScoresArr = projectSnapshots.values_list('project', flat=True)
		projectsWithoutSnapshot = projects.exclude(id__in=projectsWithScoresArr)
	except Exception as ex:
		print(ex)
		projectSnapshots = None
		projectsWithoutSnapshot = None
		
	context = {
		'breadcrumbs': [{ 'text': 'Tasks', 'url': reverse('metrics:tasks_home')}],
		'task': task,
		'tasks': Task.objects.all(),
		'projects': task.projects.all(),
		'researchItems': Artifact.objects.filter(Q(id__in=task.projects.values('artifact_projects')) | Q(id__in=task.research.values('id'))),
		'menunavItem': 'tasks',
		'goalCategory': goalCategory,
		'projectSnapshots': projectSnapshots,
		'projectsWithoutSnapshot': projectsWithoutSnapshot,
	}
	
	response = render(request, 'metrics/tasks_detail.html', context)
	return response

