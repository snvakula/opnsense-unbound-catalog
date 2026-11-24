<?php

namespace OPNsense\UnboundCatalog\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Config;
use OPNsense\UnboundCatalog\UnboundCatalog;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'catalogsync';
    protected static $internalModelClass = UnboundCatalog::class;
    protected static $internalModelUseSafeDelete = true;

    protected function getModelNodes()
    {
        return ['general' => $this->getModel()->general->getNodes()];
    }

    public function setAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed'];
        }

        $payload = $this->request->getPost('catalogsync');
        if (!is_array($payload) || !is_array($payload['general'] ?? null)) {
            return ['result' => 'failed'];
        }

        Config::getInstance()->lock();
        $general = $this->getModel()->general;
        $general->setNodes($payload['general']);
        $result = $this->validate($general, 'catalogsync.general', true);
        return empty($result['validations']) ? $this->save(false, true) : $result;
    }

    public function searchAction()
    {
        $result = $this->searchBase(
            'catalogs.catalog',
            ['enabled', 'name', 'transfer_servers', 'authoritative_servers', 'description'],
            'name'
        );
        foreach ($result['rows'] as &$row) {
            $row['last_sync'] = '';
            $row['managed_zones'] = 0;
            $row['status'] = $row['enabled'] === '1' ? gettext('Configured') : gettext('Disabled');
        }

        return $result;
    }

    public function getItemAction($uuid = null)
    {
        $result = $this->getBase('catalog', 'catalogs.catalog', $uuid);
        foreach (['transfer_servers', 'authoritative_servers', 'notify_sources'] as $field) {
            unset($result['catalog'][$field]['']);
        }

        return $result;
    }

    public function addItemAction()
    {
        return $this->addBase('catalog', 'catalogs.catalog');
    }

    public function setItemAction($uuid)
    {
        return $this->setBase('catalog', 'catalogs.catalog', $uuid);
    }

    public function delItemAction($uuid)
    {
        return $this->delBase('catalogs.catalog', $uuid);
    }
}
