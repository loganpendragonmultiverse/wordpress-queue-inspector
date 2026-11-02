<?php
/**
 * Run inside a disposable or operator-approved WP-CLI bootstrap:
 * wp eval-file exporter/wqi-export.php /absolute/output.json SITE_PSEUDONYM --hook=init
 * Only SELECTs, _get_cron_array and callback registration checks are used.
 * WordPress/plugin bootstrap itself can have side effects. Never run on a client
 * site without the operator's separate installation/collection authorization.
 */
if (!defined('WP_CLI') || !WP_CLI) { throw new RuntimeException('WP-CLI required'); }
if (!isset($args[0], $args[1])) { throw new RuntimeException('output path and site pseudonym required'); }
$output = $args[0]; $site = $args[1];
if (!preg_match('/^[A-Za-z0-9._-]{1,64}$/D', $site)) { throw new RuntimeException('invalid site pseudonym'); }
if (file_exists($output)) { throw new RuntimeException('refusing to overwrite output'); }
$started = gmdate('c'); $jobs = []; $logs = [];
$cron = _get_cron_array();
if (!is_array($cron)) { $cron = []; }
foreach ($cron as $timestamp => $hooks) {
    foreach ($hooks as $hook => $events) {
        foreach ($events as $key => $event) {
            $jobs[] = ['kind'=>'cron', 'id'=>hash('sha256', $timestamp.'|'.$hook.'|'.$key),
                'hook'=>substr($hook,0,512), 'group'=>'', 'status'=>'scheduled',
                'scheduled'=>gmdate('c',(int)$timestamp),
                'recurrence'=>substr((string)($event['schedule'] ?? ''),0,512),
                'callback_present'=>has_action($hook)!==false];
        }
    }
}
$cron_count = count($jobs); $action_count = 0; $log_count = 0; $action_complete = false; $log_complete = false;
global $wpdb;
$actions_table = $wpdb->prefix.'actionscheduler_actions';
$logs_table = $wpdb->prefix.'actionscheduler_logs';
if ($wpdb->get_var($wpdb->prepare('SHOW TABLES LIKE %s', $actions_table)) === $actions_table) {
    $action_count = (int)$wpdb->get_var("SELECT COUNT(*) FROM `$actions_table`");
    $last_id = 0; $limit = 500; $ceiling = 100000;
    while (count($jobs)-$cron_count < $ceiling) {
        $rows = $wpdb->get_results($wpdb->prepare("SELECT action_id, hook, status, scheduled_date_gmt, group_id FROM `$actions_table` WHERE action_id > %d ORDER BY action_id LIMIT %d", $last_id, $limit), ARRAY_A);
        if (!$rows) break;
        foreach ($rows as $row) {
            $last_id = (int)$row['action_id'];
            $date = strtotime($row['scheduled_date_gmt'].' UTC');
            if ($date === false) { throw new RuntimeException('invalid action schedule time'); }
            $jobs[] = ['kind'=>'action', 'id'=>(string)$last_id,
                'hook'=>substr($row['hook'],0,512), 'group'=>(string)$row['group_id'],
                'status'=>$row['status'], 'scheduled'=>gmdate('c',$date)];
        }
        if (count($rows) < $limit) break;
    }
    $exported_action_count = count($jobs)-$cron_count;
    $action_complete = $exported_action_count === $action_count;
    if ($exported_action_count > $action_count) { $action_count = null; }
    if ($wpdb->get_var($wpdb->prepare('SHOW TABLES LIKE %s', $logs_table)) === $logs_table) {
        $log_count = (int)$wpdb->get_var("SELECT COUNT(*) FROM `$logs_table`");
        $last_log = 0;
        $exported_ids = array_fill_keys(array_map(static function($job) { return $job['id']; }, array_slice($jobs,$cron_count)), true);
        while (count($logs) < 500000) {
            $rows = $wpdb->get_results($wpdb->prepare("SELECT log_id, action_id, log_date_gmt FROM `$logs_table` WHERE log_id > %d ORDER BY log_id LIMIT %d", $last_log, $limit), ARRAY_A);
            if (!$rows) break;
            foreach ($rows as $row) {
                $last_log = (int)$row['log_id'];
                $date = strtotime($row['log_date_gmt'].' UTC');
                if ($date === false) continue;
                if (isset($exported_ids[(string)$row['action_id']])) {
                    $logs[] = ['action_id'=>(string)$row['action_id'], 'message'=>'Log event (message omitted for privacy)', 'time'=>gmdate('c',$date)];
                }
            }
            if (count($rows) < $limit) break;
        }
        $log_complete = $action_complete && count($logs) === $log_count;
        if (count($logs) > $log_count) { $log_count = null; $log_complete = false; }
    }
}
$data = ['schema'=>'wqi.snapshot/1','site'=>$site,'captured_start'=>$started,'captured_end'=>gmdate('c'),
    'source'=>['wordpress'=>get_bloginfo('version'),'php'=>PHP_VERSION,'action_scheduler'=>class_exists('ActionScheduler_Versions') ? ActionScheduler_Versions::instance()->latest_version() : null,
        'disable_wp_cron'=>defined('DISABLE_WP_CRON') && DISABLE_WP_CRON],
    'coverage'=>['action'=>['complete'=>$action_complete,'source_count'=>$action_count],
        'cron'=>['complete'=>true,'source_count'=>$cron_count],
        'log'=>['complete'=>$log_complete,'source_count'=>$log_count]],
    'jobs'=>$jobs,'logs'=>$logs];
$json = json_encode($data, JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
if (strlen($json) > 64*1024*1024) { throw new RuntimeException('snapshot exceeds 64 MiB'); }
$tmp = $output.'.tmp.'.getmypid();
if (file_put_contents($tmp,$json,LOCK_EX) === false || !rename($tmp,$output)) { @unlink($tmp); throw new RuntimeException('output write failed'); }
WP_CLI::success('Wrote snapshot to '.$output.' (action complete: '.($action_complete?'yes':'no').', logs complete: '.($log_complete?'yes':'no').')');
