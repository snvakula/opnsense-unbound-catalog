<script>
    $(function () {
        const api = '/api/unboundcatalog/';
        const settings = api + 'settings/';
        const service = api + 'service/';
        const statusLabels = $('#catalog-service-status').data();

        function refreshOverview() {
            ajaxGet(service + 'overview', {}, function (data, status) {
                if (status !== 'success') {
                    $('#catalog-service-status').text(statusLabels.unknown);
                    $('#catalog-uptime, #catalog-count, #catalog-enabled-count').text('—');
                    return;
                }
                $('#catalog-service-status').text(statusLabels[data.status] || statusLabels.unknown);
                $('#catalog-uptime').text(data.uptime || '—');
                $('#catalog-count').text(data.catalogs ?? '—');
                $('#catalog-enabled-count').text(data.enabled_catalogs ?? '—');
            });
        }

        $('#grid-catalogs').UIBootgrid({
            search: settings + 'search',
            get: settings + 'get_item/',
            set: settings + 'set_item/',
            add: settings + 'add_item/',
            del: settings + 'del_item/',
            options: {selection: false, multiSelect: false}
        }).on('loaded.rs.jquery.bootgrid', refreshOverview);

        $('#grid-catalog-zones').UIBootgrid({
            search: api + 'zones/search',
            options: {selection: false, multiSelect: false}
        });

        mapDataToFormUI({frm_catalog_settings: settings + 'get'}).done(function (forms) {
            if (forms.frm_catalog_settings?.catalogsync?.general) {
                $('#frm_catalog_settings .selectpicker').selectpicker('refresh');
                $('#catalog-save').prop('disabled', false);
            }
        });

        $('#catalog-save').on('click', function () {
            saveFormToEndpoint(settings + 'set', 'frm_catalog_settings', function (data) {
                if (data.result === 'saved') {
                    $('#catalog-change-message').show();
                }
            });
        });

        $('#catalog-apply').SimpleActionButton({
            onAction: function (data, status) {
                if (status === 'success' && data.status === 'ok') {
                    $('#catalog-change-message').hide();
                }
                refreshOverview();
            }
        });

        updateServiceControlUI('unboundcatalog');
        refreshOverview();
        window.setInterval(function () {
            if (!document.hidden && $('#catalog-overview').hasClass('active')) {
                refreshOverview();
            }
        }, 10000);
    });
</script>

<ul class="nav nav-tabs" role="tablist" id="maintabs">
    <li class="active"><a data-toggle="tab" href="#catalog-overview">{{ lang._('Overview') }}</a></li>
    <li><a data-toggle="tab" href="#catalog-list">{{ lang._('Catalogs') }}</a></li>
    <li><a data-toggle="tab" href="#catalog-zones">{{ lang._('Zones') }}</a></li>
    <li><a data-toggle="tab" href="#catalog-settings">{{ lang._('Settings') }}</a></li>
    <li><a data-toggle="tab" href="#catalog-log">{{ lang._('Log File') }}</a></li>
</ul>

<div class="content-box tab-content">
    <div id="catalog-overview" class="tab-pane fade in active">
        <table class="table table-condensed">
            <tbody>
            <tr>
                <th scope="row">{{ lang._('Service status') }}</th>
                <td id="catalog-service-status"
                    data-running="{{ lang._('Running') }}"
                    data-stopped="{{ lang._('Stopped') }}"
                    data-disabled="{{ lang._('Disabled') }}"
                    data-unknown="{{ lang._('Unknown') }}">—</td>
            </tr>
            <tr><th scope="row">{{ lang._('Service uptime') }}</th><td id="catalog-uptime">—</td></tr>
            <tr><th scope="row">{{ lang._('Configured catalogs') }}</th><td id="catalog-count">—</td></tr>
            <tr><th scope="row">{{ lang._('Enabled catalogs') }}</th><td id="catalog-enabled-count">—</td></tr>
            <tr><th scope="row">{{ lang._('Catalog synchronization') }}</th><td>{{ lang._('Unavailable in version 0.1.0') }}</td></tr>
            <tr><th scope="row">{{ lang._('DNS NOTIFY listener') }}</th><td>{{ lang._('Unavailable in version 0.1.0') }}</td></tr>
            </tbody>
        </table>
        <button type="button" class="btn btn-default" disabled>{{ lang._('Synchronize now') }}</button>
    </div>

    <div id="catalog-list" class="tab-pane fade">
        <table id="grid-catalogs" class="table table-condensed table-hover table-striped table-responsive"
               data-editDialog="DialogCatalog" data-editAlert="catalog-change-message">
            <thead>
            <tr>
                <th data-column-id="uuid" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="enabled" data-formatter="boolean">{{ lang._('Enabled') }}</th>
                <th data-column-id="name">{{ lang._('Catalog name') }}</th>
                <th data-column-id="transfer_servers">{{ lang._('Transfer servers') }}</th>
                <th data-column-id="authoritative_servers">{{ lang._('Authoritative servers') }}</th>
                <th data-column-id="last_sync" data-sortable="false">{{ lang._('Last synchronization') }}</th>
                <th data-column-id="managed_zones" data-sortable="false">{{ lang._('Managed zones') }}</th>
                <th data-column-id="status" data-sortable="false">{{ lang._('Status') }}</th>
                <th data-column-id="description">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-formatter="commands" data-sortable="false">{{ lang._('Actions') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr><td><button type="button" data-action="add" class="btn btn-xs btn-default"
                            aria-label="{{ lang._('Add catalog') }}"><span class="fa fa-plus"></span></button></td></tr>
            </tfoot>
        </table>
    </div>

    <div id="catalog-zones" class="tab-pane fade">
        <p class="alert alert-info">{{ lang._('Zone discovery and runtime reconciliation are unavailable in version 0.1.0.') }}</p>
        <table id="grid-catalog-zones" class="table table-condensed table-hover table-striped table-responsive">
            <thead>
            <tr>
                <th data-column-id="zone" data-identifier="true">{{ lang._('Zone name') }}</th>
                <th data-column-id="catalog">{{ lang._('Catalog') }}</th>
                <th data-column-id="authoritative_servers">{{ lang._('Authoritative servers') }}</th>
                <th data-column-id="type">{{ lang._('Zone type') }}</th>
                <th data-column-id="dnssec">{{ lang._('DNSSEC policy') }}</th>
                <th data-column-id="status">{{ lang._('Runtime status') }}</th>
                <th data-column-id="last_change">{{ lang._('Last change') }}</th>
                <th data-column-id="error">{{ lang._('Error or conflict details') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>

    <div id="catalog-settings" class="tab-pane fade">
        <p class="alert alert-info">{{ lang._('Version 0.1.0 uses the enable switch and log level. DNS settings are saved for later milestones.') }}</p>
        {{ partial('layout_partials/base_form', ['fields': formSettings, 'id': 'frm_catalog_settings']) }}
        <button id="catalog-save" type="button" class="btn btn-default" disabled>{{ lang._('Save') }}</button>
    </div>

    <div id="catalog-log" class="tab-pane fade">
        {{ partial('OPNsense/Diagnostics/log', ['module': 'core', 'scope': 'unboundcatalog', 'service': '', 'default_log_severity': 'Informational']) }}
    </div>
</div>

<section class="page-content-main">
    <div class="content-box">
        <div class="col-md-12">
            <div id="catalog-change-message" class="alert alert-info" style="display: none" role="status">
                {{ lang._('Apply the saved changes to update the Catalog Sync service.') }}
            </div>
            <button id="catalog-apply" class="btn btn-primary" type="button"
                    data-endpoint="/api/unboundcatalog/service/reconfigure"
                    data-service-widget="unboundcatalog"
                    data-label="{{ lang._('Apply') }}"
                    data-error-title="{{ lang._('Error applying Catalog Sync settings') }}"></button>
        </div>
    </div>
</section>

{{ partial('layout_partials/base_dialog', ['fields': formCatalog, 'id': 'DialogCatalog', 'label': lang._('Edit Catalog')]) }}
