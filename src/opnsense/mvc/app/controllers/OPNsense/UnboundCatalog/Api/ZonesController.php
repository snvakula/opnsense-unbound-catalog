<?php

namespace OPNsense\UnboundCatalog\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Config;
use OPNsense\UnboundCatalog\OperationalStatus;
use OPNsense\UnboundCatalog\UnboundCatalog;

class ZonesController extends ApiControllerBase
{
    public function setEnabledAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed'];
        }
        $zone = $this->request->getPost('zone');
        $enabled = $this->request->getPost('enabled');
        if (!is_string($zone) || !in_array($enabled, ['0', '1'], true)) {
            return ['result' => 'failed'];
        }
        $zone = strtolower($zone);
        if (strlen($zone) > 254 || !preg_match('/\A(?:[a-z0-9_][a-z0-9_-]{0,62}\.)+\z/', $zone)) {
            return ['result' => 'failed'];
        }

        $config = Config::getInstance()->lock();
        $model = new UnboundCatalog();
        $zones = $this->disabledZones($model);
        if ($enabled === '1') {
            unset($zones[$zone]);
        } else {
            $zones[$zone] = true;
        }
        ksort($zones);
        $encoded = implode(',', array_keys($zones));
        if (strlen($encoded) > 65535) {
            return ['result' => 'failed', 'message' => gettext('The disabled zone list has reached its size limit.')];
        }
        if ((string)$model->disabled_zones !== $encoded) {
            $model->disabled_zones = $encoded;
            $model->serializeToConfig();
            $config->save();
        }
        return ['result' => 'saved'];
    }

    private function disabledZones(UnboundCatalog $model): array
    {
        return array_fill_keys(array_filter(explode(',', (string)$model->disabled_zones)), true);
    }

    public function searchAction()
    {
        $rows = OperationalStatus::read(true);
        $disabled = $this->disabledZones(new UnboundCatalog());
        foreach ($rows as &$row) {
            $row['disabled_by_user'] = isset($disabled[$row['zone']]);
            $row['enabled'] = $row['disabled_by_user'] ? '0' : '1';
            $row['type'] = gettext('Stub Zone');
            $row['status'] = OperationalStatus::label($row['status']);
            $row['dnssec'] = match ($row['dnssec']) {
                true => gettext('Insecure exception'),
                false => gettext('Resolver default'),
                default => '',
            };
            $row['runtime_dnssec'] = match ($row['runtime_dnssec']) {
                true => gettext('Insecure exception'),
                false => gettext('Validation enabled'),
                default => '',
            };
            $row['last_change'] = OperationalStatus::timestamp($row['last_change']);
        }
        unset($row);
        return $this->searchRecordsetBase($rows, null, 'zone');
    }
}
