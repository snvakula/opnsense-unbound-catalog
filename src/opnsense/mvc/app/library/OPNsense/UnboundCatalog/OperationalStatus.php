<?php

namespace OPNsense\UnboundCatalog;

use OPNsense\Core\Backend;

class OperationalStatus
{
    public static function read(bool $zones = false): array
    {
        $command = $zones ? 'unboundcatalog zones' : 'unboundcatalog summary';
        $data = json_decode((new Backend())->configdRun($command), true);
        return is_array($data) ? $data : [];
    }

    public static function label(string $status): string
    {
        return match ($status) {
            'active' => gettext('Active'),
            'pending' => gettext('Pending'),
            'conflict' => gettext('Conflict'),
            'error' => gettext('Error'),
            'stale' => gettext('Stale'),
            'disabled' => gettext('Disabled'),
            default => gettext('Unknown'),
        };
    }

    public static function timestamp($seconds): string
    {
        return is_numeric($seconds) && $seconds > 0 ? date('Y-m-d H:i:s', (int)$seconds) : '';
    }
}
