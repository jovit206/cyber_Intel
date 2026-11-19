[CmdletBinding()]
param([string]$BaseUrl = 'http://localhost:8000')

$ErrorActionPreference = 'Continue'
$script:Failures = 0

function Report([string]$State, [string]$Name, [string]$Detail) {
  Write-Host "[$State] $Name - $Detail"
  if ($State -eq 'FAIL') { $script:Failures++ }
}

function Check-Get([string]$Path, [string]$Name) {
  try {
    $response = Invoke-WebRequest -Uri "$BaseUrl$Path" -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -eq 200) { Report 'PASS' $Name "HTTP $($response.StatusCode)"; return $response }
    Report 'FAIL' $Name "HTTP $($response.StatusCode)"
  } catch { Report 'FAIL' $Name $_.Exception.Message }
  return $null
}

$health = Check-Get '/health' 'Backend health'
$frontend = Check-Get '/' 'Frontend availability'
$accountPage = Check-Get '/account' 'Separate account page'
$accountScript = Check-Get '/account.js' 'Account frontend script'
$plansResponse = Check-Get '/api/subscriptions/plans' 'Subscription plans'

foreach ($path in @('/api/meta', '/api/events?page=1&limit=1', '/api/lab10/overview', '/api/lab8/status', '/api/lab9/latency', '/api/subscriptions/financial-activity')) {
  try {
    Invoke-WebRequest -Uri "$BaseUrl$path" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
    Report 'FAIL' "Protected route $path" 'Anonymous request unexpectedly succeeded'
  } catch {
    $code = [int]$_.Exception.Response.StatusCode
    Report $(if ($code -eq 401) { 'PASS' } else { 'FAIL' }) "Protected route $path" "HTTP $code for anonymous request"
  }
}
if ($accountPage -and $accountPage.Content -match 'account\.js\?v=') { Report 'PASS' 'Account UI asset' 'Versioned account script is referenced' }
elseif ($accountPage) { Report 'FAIL' 'Account UI asset' 'Versioned account script is not referenced' }
if ($accountScript) { Report 'PASS' 'Account UI script' 'HTTP 200' }
if ($plansResponse) {
  $plans = $plansResponse.Content | ConvertFrom-Json
  Report $(if ($plans.plans.Count -eq 3) { 'PASS' } else { 'FAIL' }) 'Plan catalog' "$($plans.plans.Count) plans returned"
}
try {
  Invoke-WebRequest -Uri "$BaseUrl/api/subscriptions/me" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
  Report 'FAIL' 'Unauthenticated account isolation' 'Profile endpoint unexpectedly accepted an anonymous request'
} catch {
  $code = [int]$_.Exception.Response.StatusCode
  Report $(if ($code -eq 401) { 'PASS' } else { 'FAIL' }) 'Unauthenticated account isolation' "HTTP $code"
}
try {
  $body = '{"amount":100,"idempotency_key":"health-probe-auth-01"}'
  Invoke-WebRequest -Uri "$BaseUrl/api/subscriptions/wallet/top-up" -Method Post -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop | Out-Null
  Report 'FAIL' 'Wallet top-up authentication' 'Anonymous top-up unexpectedly succeeded'
} catch {
  $code = [int]$_.Exception.Response.StatusCode
  Report $(if ($code -eq 401) { 'PASS' } else { 'FAIL' }) 'Wallet top-up authentication' "HTTP $code; no wallet write was attempted"
}

$mongo = Get-Command docker -ErrorAction SilentlyContinue
if ($mongo) {
  $rsOutput = & docker exec fresh-mongo1 mongosh --quiet --eval 'const s=rs.status();print(JSON.stringify({set:s.set,healthy:s.members.filter(m=>m.health===1).length,primary:s.members.filter(m=>/^PRIMARY$/.test(m.stateStr)&&m.health===1).length}))' 2>$null
  if ($LASTEXITCODE -eq 0) {
    $line = $rsOutput | Where-Object { ([string]$_).Trim().StartsWith('{') } | Select-Object -Last 1
    $rs = $line | ConvertFrom-Json
    Report $(if ($rs.set -eq 'rs0' -and $rs.healthy -eq 3 -and $rs.primary -eq 1) { 'PASS' } else { 'FAIL' }) 'MongoDB replica set' "$($rs.set), $($rs.healthy) healthy members, $($rs.primary) primary"
    $txnOutput = & docker exec fresh-mongo1 mongosh --quiet --eval 'const s=db.getMongo().startSession();const name=String.fromCharCode(99,104,97,112,116,101,114,48,55);const c=s.getDatabase(name).subscription_sessions;const token=String.fromCharCode(104,101,97,108,116,104,45,112,114,111,98,101,45)+new ObjectId().toHexString();s.startTransaction({readConcern:{level:String.fromCharCode(115,110,97,112,115,104,111,116)},writeConcern:{w:String.fromCharCode(109,97,106,111,114,105,116,121)}});c.insertOne({token_hash:token,user_id:token,expires_at:new Date(Date.now()+60000)});c.deleteOne({token_hash:token});s.commitTransaction();s.endSession();print(1)' 2>$null
    if ($LASTEXITCODE -eq 0 -and @($txnOutput | Where-Object { ([string]$_).Trim() -eq '1' }).Count -gt 0) {
      Report 'PASS' 'Replica-set transaction readiness' 'Snapshot/majority transaction committed a no-net-change probe'
    } else { Report 'FAIL' 'Replica-set transaction readiness' 'Transaction probe did not commit' }
  } else { Report 'FAIL' 'MongoDB replica set' 'Could not query the existing fresh-mongo1 container' }
} else { Report 'SKIP' 'MongoDB replica set' 'Docker CLI is not available' }

foreach ($item in @('Demo login with supplied credentials', 'Session expiry/TTL behavior',
  'Credit limits and plan restrictions', 'Subscription purchase commit/rollback',
  'Wallet transfer atomicity and idempotency', 'Concurrent inventory reservations',
  'Retry/backoff under injected transient errors', 'Receipt/invoice ownership',
  'Administrator revenue and consistency audit', 'Authenticated dashboard data visibility')) {
  Report 'SKIP' $item 'Requires initialized demo accounts or isolated transaction fixtures; no financial records were created by this check'
}

if ($script:Failures -gt 0) { Write-Host "Health check finished with $($script:Failures) failure(s)."; exit 1 }
Write-Host 'Health check completed; SKIP items require the remaining implementation/demo setup.'
