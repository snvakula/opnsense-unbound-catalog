<?php

namespace OPNsense\UnboundCatalog;

class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->formSettings = $this->getForm('settings');
        $this->view->formCatalog = $this->getForm('dialogCatalog');
        $this->view->pick('OPNsense/UnboundCatalog/index');
    }
}
