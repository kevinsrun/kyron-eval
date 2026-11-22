<?php
// Local provisioning only. Uses OpenEMR's client repository, as its official test-client CLI does.
// No patient data or direct credential-table writes. Credentials go only to captured local stdout.
if (PHP_SAPI !== 'cli' || getenv('MYSQL_DATABASE') !== 'kyron_synthetic') {
    throw new RuntimeException('Only the isolated Kyron synthetic container may register this client');
}
$_GET['site'] = 'default';
$ignoreAuth = true;
$sessionAllowWrite = true;
require_once '/var/www/localhost/htdocs/openemr/interface/globals.php';
$repo = new \OpenEMR\Common\Auth\OpenIDConnect\Repositories\ClientRepository();
$clientId = $repo->generateClientId();
$secret = $repo->generateClientSecret();
$scopes = 'openid api:oemr api:fhir user/patient.crus user/appointment.cruds user/facility.crus user/practitioner.crus user/user.rs user/Patient.rs user/Appointment.rs user/Condition.rs user/MedicationRequest.rs user/Observation.rs';
$info = [
 'client_role'=>'user','client_name'=>'Kyron isolated synthetic evaluation',
 'client_secret'=>$secret,'registration_access_token'=>$repo->generateRegistrationAccessToken(),
 'registration_client_uri_path'=>$repo->generateRegistrationClientUriPath(),
 'contacts'=>'synthetic-admin@example.invalid','redirect_uris'=>['https://localhost:9310/callback'],
 'grant_types'=>'password','scope'=>$scopes,'dsi_type'=>0
];
if (!$repo->insertNewClient($clientId,$info,'default')) {
 throw new RuntimeException('Synthetic client registration failed');
}
$repo->saveIsEnabled($repo->getClientEntity($clientId),true);
echo json_encode(['client_id'=>$clientId,'client_secret'=>$secret,'scope'=>$scopes]);
