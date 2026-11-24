<?php

namespace OPNsense\UnboundCatalog\Api;

use OPNsense\Base\ApiControllerBase;

class ZonesController extends ApiControllerBase
{
    public function searchAction()
    {
        return [
            'rows' => [],
            'rowCount' => 0,
            'total' => 0,
            'current' => 1,
        ];
    }
}
