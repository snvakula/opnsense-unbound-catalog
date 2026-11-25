<?php

namespace OPNsense\UnboundCatalog\Migrations;

use OPNsense\Base\BaseModelMigration;

class M0_2_2 extends BaseModelMigration
{
    public function run($model)
    {
        $general = $model->general;
        $initial = max(300, (int)(string)$general->retry_initial);
        $general->setNodes([
            'sync_interval' => max(300, (int)(string)$general->sync_interval),
            'retry_initial' => $initial,
            'retry_max' => max($initial, (int)(string)$general->retry_max),
        ]);
    }
}
