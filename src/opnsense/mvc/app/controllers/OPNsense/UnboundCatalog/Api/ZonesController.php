<?php

namespace OPNsense\UnboundCatalog\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\UnboundCatalog\OperationalStatus;

class ZonesController extends ApiControllerBase
{
    public function searchAction()
    {
        $rows = OperationalStatus::read(true);
        foreach ($rows as &$row) {
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
