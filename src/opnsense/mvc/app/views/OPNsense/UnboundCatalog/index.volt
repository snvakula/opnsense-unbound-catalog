<script>
    $(function () {
        const api = '/api/unboundcatalog/';
        const settings = api + 'settings/';
        const service = api + 'service/';
        const statusLabels = $('#catalog-service-status').data();

        function refreshOverview() {
            ajaxGet(service + 'overview', {}, function (data, status) {
                const running = status === 'success' && data.status === 'running';
                const values = status === 'success' ? data : {};
                $('#catalog-service-status').text(statusLabels[values.status] || statusLabels.unknown);
                $('#catalog-sync').prop('disabled', !running);
                $('#catalog-uptime').text(values.uptime || '—');
                $('#catalog-count').text(values.catalogs ?? '—');
                $('#catalog-enabled-count').text(values.enabled_catalogs ?? '—');
                for (const key of ['managed', 'active', 'conflict', 'pending']) {
                    $('#catalog-' + key).text(values.counts?.[key] ?? '—');
                }
                for (const key of ['attempted', 'succeeded', 'next']) {
                    $('#catalog-' + key).text(values[key] || '—');
                }
                const listener = $('#catalog-listener');
                const families = (values.listener || []).map((family) => ({AF_INET: 'IPv4', AF_INET6: 'IPv6'}[family]));
                if (families.length) {
                    const label = families.length < values.notify_families ? listener.data('partial') + ' · ' : '';
                    listener.text(label + families.join(', ') + ' · UDP ' + values.notify_port);
                } else {
                    const state = values.notify_enabled === undefined ? 'unknown' :
                        values.notify_enabled ? (running ? 'unavailable' : 'stopped') : 'disabled';
                    listener.text(listener.data(state));
                }
                const configuration = $('#catalog-configuration');
                configuration.text(configuration.data(values.configuration || 'unknown') || configuration.data('unknown'));
                $('#catalog-error').text(values.last_error || '—');
            });
        }

        $('#catalog-sync').SimpleActionButton({onAction: refreshOverview});

        $('#grid-catalogs').UIBootgrid({
            search: settings + 'search',
            get: settings + 'get_item/',
            set: settings + 'set_item/',
            add: settings + 'add_item/',
            del: settings + 'del_item/',
            toggle: settings + 'toggle_item/'
        }).on('loaded.rs.jquery.bootgrid', refreshOverview);

        const zoneLabels = $('#grid-catalog-zones').data();

        function setZoneEnabled(enabled, event) {
            event.stopPropagation();
            const button = $(event.currentTarget);
            button.prop('disabled', true);
            ajaxCall(api + 'zones/set_enabled', {
                zone: button.attr('data-row-id'),
                enabled
            }, function (data, status) {
                button.prop('disabled', false);
                if (status === 'success' && data?.result === 'saved') {
                    $('#catalog-change-message').show();
                    zones.bootgrid('reload');
                } else {
                    const message = $('<span>').text(data?.message || zoneLabels.failure);
                    BootstrapDialog.show({type: BootstrapDialog.TYPE_WARNING, message});
                }
            });
        }

        const zones = $('#grid-catalog-zones').UIBootgrid({
            search: api + 'zones/search',
            options: {selection: false, multiSelect: false},
            commands: {
                'enable-zone': {
                    classname: 'fa fa-fw fa-check-square-o',
                    title: zoneLabels.enable,
                    filter: (cell) => cell.getData().disabled_by_user,
                    method: setZoneEnabled.bind(null, '1')
                },
                'disable-zone': {
                    classname: 'fa fa-fw fa-square-o',
                    title: zoneLabels.disable,
                    filter: (cell) => !cell.getData().disabled_by_user,
                    method: setZoneEnabled.bind(null, '0')
                }
            }
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
                    zones.bootgrid('reload');
                }
                refreshOverview();
            }
        });

        $('a[href="#catalog-overview"]').on('shown.bs.tab', refreshOverview);
        $('a[href="#catalog-zones"]').on('shown.bs.tab', function () {
            $('#grid-catalog-zones').bootgrid('reload');
        });
        $('a[href="#catalog-list"]').on('shown.bs.tab', function () {
            $('#grid-catalogs').bootgrid('reload');
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
            <tr><th scope="row">{{ lang._('Managed zones') }}</th><td id="catalog-managed">—</td></tr>
            <tr><th scope="row">{{ lang._('Active zones') }}</th><td id="catalog-active">—</td></tr>
            <tr><th scope="row">{{ lang._('Conflicting zones') }}</th><td id="catalog-conflict">—</td></tr>
            <tr><th scope="row">{{ lang._('Pending or failed zones') }}</th><td id="catalog-pending">—</td></tr>
            <tr><th scope="row">{{ lang._('Last synchronization attempt') }}</th><td id="catalog-attempted">—</td></tr>
            <tr><th scope="row">{{ lang._('Last successful synchronization') }}</th><td id="catalog-succeeded">—</td></tr>
            <tr><th scope="row">{{ lang._('Next scheduled synchronization') }}</th><td id="catalog-next">—</td></tr>
            <tr>
                <th scope="row">{{ lang._('DNS NOTIFY listener') }}</th>
                <td id="catalog-listener"
                    data-stopped="{{ lang._('Stopped') }}"
                    data-partial="{{ lang._('Partially available') }}"
                    data-unavailable="{{ lang._('Unavailable') }}"
                    data-disabled="{{ lang._('Disabled') }}"
                    data-unknown="{{ lang._('Unknown') }}">—</td>
            </tr>
            <tr>
                <th scope="row">{{ lang._('Unbound configuration') }}</th>
                <td id="catalog-configuration"
                    data-synced="{{ lang._('Synchronized') }}"
                    data-pending="{{ lang._('Pending') }}"
                    data-conflict="{{ lang._('Conflict') }}"
                    data-error="{{ lang._('Error') }}"
                    data-unknown="{{ lang._('Unknown') }}">—</td>
            </tr>
            <tr><th scope="row">{{ lang._('Last error') }}</th><td id="catalog-error">—</td></tr>
            </tbody>
        </table>
        <button id="catalog-sync" type="button" class="btn btn-default" disabled
                data-endpoint="/api/unboundcatalog/service/sync"
                data-label="{{ lang._('Synchronize now') }}"
                data-error-title="{{ lang._('Error synchronizing catalogs') }}"></button>
    </div>

    <div id="catalog-list" class="tab-pane fade">
        <p>{{ lang._('Apply after disabling or deleting a catalog to withdraw its zones from Unbound.') }}</p>
        <table id="grid-catalogs" class="table table-condensed table-hover table-striped" style="visibility: hidden"
               data-editDialog="DialogCatalog" data-editAlert="catalog-change-message">
            <thead>
            <tr>
                <th data-column-id="uuid" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                <th data-column-id="enabled" data-formatter="rowtoggle" data-width="100">{{ lang._('Enabled') }}</th>
                <th data-column-id="name">{{ lang._('Catalog name') }}</th>
                <th data-column-id="transfer_servers">{{ lang._('Transfer servers') }}</th>
                <th data-column-id="authoritative_servers">{{ lang._('Authoritative servers') }}</th>
                <th data-column-id="last_sync" data-sortable="false">{{ lang._('Last synchronization') }}</th>
                <th data-column-id="serial" data-visible="false" data-sortable="false">{{ lang._('SOA serial') }}</th>
                <th data-column-id="managed_zones" data-sortable="false">{{ lang._('Managed zones') }}</th>
                <th data-column-id="status" data-sortable="false">{{ lang._('Status') }}</th>
                <th data-column-id="description">{{ lang._('Description') }}</th>
                <th data-column-id="commands" data-width="100" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
            <tfoot>
            <tr>
                <td></td>
                <td>
                    <button type="button" data-action="add" class="btn btn-xs btn-primary"
                            aria-label="{{ lang._('Add catalog') }}"><span class="fa fa-plus fa-fw"></span></button>
                    <button type="button" data-action="deleteSelected" class="btn btn-xs btn-default"
                            aria-label="{{ lang._('Delete selected') }}"><span class="fa fa-trash-o fa-fw"></span></button>
                </td>
            </tr>
            </tfoot>
        </table>
    </div>

    <div id="catalog-zones" class="tab-pane fade">
        <table id="grid-catalog-zones" class="table table-condensed table-hover table-striped" style="visibility: hidden"
               data-enable="{{ lang._('Enable zone') }}"
               data-disable="{{ lang._('Disable zone') }}"
               data-failure="{{ lang._('Could not save the zone setting.') }}">
            <thead>
            <tr>
                <th data-column-id="zone" data-identifier="true">{{ lang._('Zone name') }}</th>
                <th data-column-id="catalog">{{ lang._('Catalog') }}</th>
                <th data-column-id="authoritative_servers">{{ lang._('Authoritative servers') }}</th>
                <th data-column-id="type" data-visible="false">{{ lang._('Zone type') }}</th>
                <th data-column-id="dnssec">{{ lang._('DNSSEC policy') }}</th>
                <th data-column-id="runtime_servers" data-visible="false">{{ lang._('Runtime addresses') }}</th>
                <th data-column-id="runtime_dnssec" data-visible="false">{{ lang._('Runtime DNSSEC policy') }}</th>
                <th data-column-id="status">{{ lang._('Runtime status') }}</th>
                <th data-column-id="last_change">{{ lang._('Last change') }}</th>
                <th data-column-id="error">{{ lang._('Error or conflict details') }}</th>
                <th data-column-id="commands" data-width="90" data-formatter="commands" data-sortable="false">{{ lang._('Commands') }}</th>
            </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>

    <div id="catalog-settings" class="tab-pane fade">
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
