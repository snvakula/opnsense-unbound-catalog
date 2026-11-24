<?php

namespace OPNsense\UnboundCatalog\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;
use OPNsense\Core\Backend;
use OPNsense\UnboundCatalog\OperationalStatus;
use OPNsense\UnboundCatalog\UnboundCatalog;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceName = 'unboundcatalog';
    protected static $internalServiceClass = UnboundCatalog::class;

    protected function serviceEnabled()
    {
        return $this->getModel()->isEnabled();
    }

    public function reconfigureAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }

        if ($this->getModel()->validate(null, '', true)) {
            return [
                'status' => 'failed',
                'status_msg' => gettext('Correct invalid catalog settings before applying.'),
            ];
        }

        $result = trim((new Backend())->configdRun('unboundcatalog apply'));
        if ($result !== 'OK') {
            return [
                'status' => 'failed',
                'status_msg' => gettext('Could not apply Catalog Sync settings. Check the system log.'),
            ];
        }

        return ['status' => 'ok'];
    }

    public function syncAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed'];
        }

        if (trim((new Backend())->configdRun('unboundcatalog sync')) !== 'OK') {
            return [
                'status' => 'failed',
                'status_msg' => gettext('Start Catalog Sync before requesting synchronization.'),
            ];
        }
        return ['status' => 'ok'];
    }

    public function overviewAction()
    {
        $result = $this->statusAction();
        $result['catalogs'] = 0;
        $result['enabled_catalogs'] = 0;
        foreach ($this->getModel()->catalogs->catalog->iterateItems() as $catalog) {
            $result['catalogs']++;
            if ((string)$catalog->enabled === '1') {
                $result['enabled_catalogs']++;
            }
        }

        $result['uptime'] = null;
        if ($result['status'] === 'running') {
            $uptime = trim((new Backend())->configdRun('unboundcatalog uptime'));
            if (preg_match('/^[0-9:-]+$/D', $uptime)) {
                $result['uptime'] = $uptime;
            }
        }

        $summary = OperationalStatus::read();
        if ($result['status'] !== 'running') {
            $summary['listener'] = [];
            $summary['next'] = null;
        }
        unset($summary['catalog_status']);
        foreach (['attempted', 'succeeded', 'next'] as $key) {
            $summary[$key] = OperationalStatus::timestamp($summary[$key] ?? null);
        }
        return $result + $summary;
    }
}
