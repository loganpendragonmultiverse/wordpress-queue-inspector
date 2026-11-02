<?php
// Disposable wpdb failure simulation; never boots a real site.
define('WP_CLI', true);
define('ARRAY_A', 'ARRAY_A');
class WP_CLI { public static function success($message) {} }
function _get_cron_array() {
    if ($GLOBALS['scenario'] === 'cron-error') return false;
    return $GLOBALS['scenario'] === 'job-boundary' ? [1700000000=>['fixture_hook'=>['opaque_key'=>['schedule'=>'hourly']]]] : [];
}
function has_action($hook) { return true; }
function get_bloginfo($name) { return 'test'; }
class FakeWpdb {
    public $prefix = 'wp_';
    public $last_error = '';
    public function prepare($query, ...$values) {
        foreach ($values as $value) {
            $query = preg_replace('/%[ds]/', is_int($value) ? (string)$value : "'".$value."'", $query, 1);
        }
        return $query;
    }
    public function get_var($query) {
        $this->last_error = '';
        if (strpos($query, 'SHOW TABLES LIKE') !== false) {
            foreach (['actions', 'claims', 'logs'] as $kind) {
                $table = 'wp_actionscheduler_'.$kind;
                if (strpos($query, $table) !== false) return $table;
            }
        }
        if (strpos($query, 'COUNT(*)') !== false) {
            if ($GLOBALS['scenario'] === 'action-count-error' && strpos($query, 'actionscheduler_actions') !== false) {
                $this->last_error = 'synthetic count error'; return null;
            }
            if ($GLOBALS['scenario'] === 'log-count-error' && strpos($query, 'actionscheduler_logs') !== false) {
                $this->last_error = 'synthetic log count error'; return null;
            }
            if ($GLOBALS['scenario'] === 'job-boundary' && strpos($query, 'actionscheduler_actions') !== false) return 100000;
            return $GLOBALS['scenario'] === 'action-page-error' && strpos($query, 'actionscheduler_actions') !== false ? 1 : 0;
        }
        return null;
    }
    public function get_results($query, $format) {
        $this->last_error = '';
        if ($GLOBALS['scenario'] === 'action-page-error' && strpos($query, 'actionscheduler_actions') !== false) {
            $this->last_error = 'synthetic page error'; return null;
        }
        if ($GLOBALS['scenario'] === 'job-boundary' && strpos($query, 'actionscheduler_actions') !== false) {
            preg_match('/a.action_id > (\d+)/', $query, $match);
            $start = (int)$match[1] + 1;
            $rows = [];
            for ($id = $start; $id < min($start + 500, 100001); $id++) {
                $rows[] = ['action_id'=>$id,'hook'=>'fixture','status'=>'pending','scheduled_date_gmt'=>'2026-11-02 00:00:00','group_id'=>1,'claim_id'=>0];
            }
            return $rows;
        }
        return [];
    }
}
$scenario = $argv[1] ?? 'healthy';
$GLOBALS['scenario'] = $scenario;
$wpdb = new FakeWpdb();
$output = tempnam(sys_get_temp_dir(), 'wqi-test-');
unlink($output);
$args = [$output, 'fixture'];
require __DIR__.'/../exporter/wqi-export.php';
$data = json_decode(file_get_contents($output), true);
unlink($output);
$part = ['action-count-error'=>'action', 'action-page-error'=>'action', 'log-count-error'=>'log', 'cron-error'=>'cron'][$scenario] ?? null;
if ($part && $data['coverage'][$part]['complete'] !== false) {
    fwrite(STDERR, "$scenario incorrectly claimed complete coverage\n"); exit(1);
}
if (!$part && (!$data['coverage']['action']['complete'] || !$data['coverage']['cron']['complete'] || !$data['coverage']['log']['complete'])) {
    if ($scenario === 'job-boundary' && count($data['jobs']) === 100000 && $data['coverage']['action']['complete'] === false && $data['coverage']['action']['source_count'] === 100000) {
        echo "PASS $scenario\n"; exit(0);
    }
    fwrite(STDERR, 'healthy empty fixture should be complete'); exit(1);
}
echo "PASS $scenario\n";
