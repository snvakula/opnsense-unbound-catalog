<?php

namespace OPNsense\UnboundCatalog;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;

class UnboundCatalog extends BaseModel
{
    public function isEnabled(): bool
    {
        if ((string)$this->general->enabled !== '1') {
            return false;
        }

        foreach ($this->catalogs->catalog->iterateItems() as $catalog) {
            if (
                (string)$catalog->enabled === '1' &&
                (string)$catalog->name !== '' &&
                (string)$catalog->transfer_servers !== '' &&
                (string)$catalog->authoritative_servers !== ''
            ) {
                return true;
            }
        }

        return false;
    }

    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);
        if (
            (string)$this->general->notify_enabled === '1' &&
            (string)$this->general->listen_ipv4 !== '1' &&
            (string)$this->general->listen_ipv6 !== '1'
        ) {
            $messages->appendMessage(new Message(
                gettext('Enable IPv4 or IPv6 to receive DNS NOTIFY.'),
                'general.listen_ipv4'
            ));
        }

        if ((int)(string)$this->general->retry_initial > (int)(string)$this->general->retry_max) {
            $messages->appendMessage(new Message(
                gettext('Set the maximum retry delay at or above the initial delay.'),
                'general.retry_max'
            ));
        }

        $names = [];
        foreach ($this->catalogs->catalog->iterateItems() as $catalog) {
            $name = strtolower(rtrim((string)$catalog->name, '.'));
            $names[$name][] = $catalog->name;
        }

        foreach ($names as $name => $fields) {
            if ($name !== '' && count($fields) > 1) {
                foreach ($fields as $field) {
                    $messages->appendMessage(new Message(
                        gettext('A catalog with this name already exists.'),
                        $field->__reference
                    ));
                }
            }
        }

        return $messages;
    }
}
