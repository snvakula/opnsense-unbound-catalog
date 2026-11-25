<?php

function unboundcatalog_configure()
{
    return [
        'bootup' => ['unboundcatalog_bootup'],
        'unbound_start' => ['unboundcatalog_restore'],
    ];
}

function unboundcatalog_bootup($verbose)
{
    $result = trim(configd_run('unboundcatalog apply'));
    if ($result !== 'OK') {
        log_error('Could not apply Catalog Sync settings at boot.');
    }
}

function unboundcatalog_restore($verbose)
{
    if (!unbound_enabled()) {
        return;
    }
    if (trim(configd_run('unboundcatalog restore')) !== 'OK') {
        log_error('Could not restore Catalog Sync zones after starting Unbound.');
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
