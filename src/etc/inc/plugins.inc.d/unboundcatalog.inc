<?php

function unboundcatalog_configure()
{
    return ['bootup' => ['unboundcatalog_bootup']];
}

function unboundcatalog_bootup($verbose)
{
    $result = trim(configd_run('unboundcatalog apply'));
    if ($result !== 'OK') {
        log_error('Could not apply Catalog Sync settings at boot.');
    }
}

function unboundcatalog_services()
{
    $model = new \OPNsense\UnboundCatalog\UnboundCatalog();
    if (!$model->isEnabled()) {
        return [];
    }

    return [[
        'name' => 'unboundcatalog',
        'description' => gettext('Unbound Catalog Sync'),
        'pidfile' => '/var/run/unbound_catalog.pid',
        'configd' => [
            'start' => ['unboundcatalog start'],
            'stop' => ['unboundcatalog stop'],
            'restart' => ['unboundcatalog restart'],
        ],
    ]];
}
