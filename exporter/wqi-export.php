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
$cron_complete = is_array($cron);
if (!$cron_complete) { $cron = []; }
foreach ($cron as $timestamp => $hooks) {
    foreach ($hooks as $hook => $events) {
        foreach ($events as $key => $event) {
            $jobs[] = ['kind'=>'cron', 'id'=>hash('sha256', $timestamp.'|'.$hook.'|'.$key),
                'hook'=>substr($hook,0,512), 'group'=>'', 'status'=>'scheduled',
                'scheduled'=>gmdate('c',(int)$timestamp),
                'recurrence'=>substr((string)($event['schedule'] ?? ''),0,512),
                'arg_fingerprint'=>hash('sha256',(string)$key),
                'callback_present'=>has_action($hook)!==false];
        }
    }
}
$cron_count = count($jobs); $action_count = 0; $log_count = 0; $action_complete = false; $log_complete = false;
if ($cron_count > 100000) { throw new RuntimeException('WP-Cron events exceed the 100000-job snapshot limit'); }
global $wpdb;
$actions_table = $wpdb->prefix.'actionscheduler_actions';
$logs_table = $wpdb->prefix.'actionscheduler_logs';
$claims_table = $wpdb->prefix.'actionscheduler_claims';
if ($wpdb->get_var($wpdb->prepare('SHOW TABLES LIKE %s', $actions_table)) === $actions_table) {
    $claims_available = $wpdb->get_var($wpdb->prepare('SHOW TABLES LIKE %s', $claims_table)) === $claims_table;
    $count_result = $wpdb->get_var("SELECT COUNT(*) FROM `$actions_table`");
    $action_count = $wpdb->last_error || $count_result === null ? null : (int)$count_result;
    $last_id = 0; $limit = 500; $ceiling = 100000 - $cron_count;
    $action_read_error = false;
    while (count($jobs)-$cron_count < $ceiling) {
        $claim_select = $claims_available ? ', c.date_created_gmt AS claim_created_gmt' : '';
        $claim_join = $claims_available ? " LEFT JOIN `$claims_table` c ON c.claim_id = a.claim_id" : '';
        $rows = $wpdb->get_results($wpdb->prepare("SELECT a.action_id, a.hook, a.status, a.scheduled_date_gmt, a.group_id, a.claim_id$claim_select FROM `$actions_table` a$claim_join WHERE a.action_id > %d ORDER BY a.action_id LIMIT %d", $last_id, $limit), ARRAY_A);
        if ($wpdb->last_error || !is_array($rows)) { $action_read_error = true; break; }
        if (!$rows) break;
        foreach ($rows as $row) {
            if (count($jobs)-$cron_count >= $ceiling) break;
            $last_id = (int)$row['action_id'];
            $date = strtotime($row['scheduled_date_gmt'].' UTC');
            if ($date === false) { throw new RuntimeException('invalid action schedule time'); }
            $job = ['kind'=>'action', 'id'=>(string)$last_id,
                'hook'=>substr($row['hook'],0,512), 'group'=>(string)$row['group_id'],
                'status'=>$row['status'], 'scheduled'=>gmdate('c',$date), 'claim_id'=>(string)(int)$row['claim_id']];
            if (!empty($row['claim_created_gmt'])) {
                $claim_date = strtotime($row['claim_created_gmt'].' UTC');
                if ($claim_date !== false) { $job['claim_created'] = gmdate('c',$claim_date); }
            }
            $jobs[] = $job;
        }
        if (count($rows) < $limit) break;
    }
    $exported_action_count = count($jobs)-$cron_count;
    $action_complete = !$action_read_error && $action_count !== null && $exported_action_count === $action_count;
    if ($action_count !== null && $exported_action_count > $action_count) { $action_count = null; }
    if ($wpdb->get_var($wpdb->prepare('SHOW TABLES LIKE %s', $logs_table)) === $logs_table) {
        $log_count_result = $wpdb->get_var("SELECT COUNT(*) FROM `$logs_table`");
        $log_count = $wpdb->last_error || $log_count_result === null ? null : (int)$log_count_result;
        $last_log = 0;
        $log_read_error = false;
        $exported_ids = array_fill_keys(array_map(static function($job) { return $job['id']; }, array_slice($jobs,$cron_count)), true);
        while (count($logs) < 500000) {
            $rows = $wpdb->get_results($wpdb->prepare("SELECT log_id, action_id, log_date_gmt FROM `$logs_table` WHERE log_id > %d ORDER BY log_id LIMIT %d", $last_log, $limit), ARRAY_A);
            if ($wpdb->last_error || !is_array($rows)) { $log_read_error = true; break; }
            if (!$rows) break;
            foreach ($rows as $row) {
                $last_log = (int)$row['log_id'];
                $date = strtotime($row['log_date_gmt'].' UTC');
                if ($date === false) continue;
                if (isset($exported_ids[(string)$row['action_id']])) {
                    if (count($logs) >= 500000) break 2;
                    $logs[] = ['action_id'=>(string)$row['action_id'], 'message'=>'Log event (message omitted for privacy)', 'time'=>gmdate('c',$date)];
                }
            }
            if (count($rows) < $limit) break;
        }
        $log_complete = !$log_read_error && $action_complete && $log_count !== null && count($logs) === $log_count;
        if ($log_count !== null && count($logs) > $log_count) { $log_count = null; $log_complete = false; }
    }
}
$data = ['schema'=>'wqi.snapshot/1','site'=>$site,'captured_start'=>$started,'captured_end'=>gmdate('c'),
    'source'=>['wordpress'=>get_bloginfo('version'),'php'=>PHP_VERSION,'action_scheduler'=>class_exists('ActionScheduler_Versions') ? ActionScheduler_Versions::instance()->latest_version() : null,
        'disable_wp_cron'=>defined('DISABLE_WP_CRON') && DISABLE_WP_CRON],
    'coverage'=>['action'=>['complete'=>$action_complete,'source_count'=>$action_count],
        'cron'=>['complete'=>$cron_complete,'source_count'=>$cron_complete ? $cron_count : null],
        'log'=>['complete'=>$log_complete,'source_count'=>$log_count]],
    'jobs'=>$jobs,'logs'=>$logs];
$json = json_encode($data, JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
if (strlen($json) > 64*1024*1024) { throw new RuntimeException('snapshot exceeds 64 MiB'); }
$tmp = $output.'.tmp.'.getmypid();
if (file_put_contents($tmp,$json,LOCK_EX) === false || !rename($tmp,$output)) { @unlink($tmp); throw new RuntimeException('output write failed'); }
WP_CLI::success('Wrote snapshot to '.$output.' (action complete: '.($action_complete?'yes':'no').', logs complete: '.($log_complete?'yes':'no').')');
