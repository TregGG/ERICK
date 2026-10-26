// DEPRECATED legacy in-memory development host. FastAPI (backend/) is now the only
// supported backend; this file is kept for reference only. It does not implement the
// current API, so the current passenger/driver apps do not work against it. See
// docs/OPERATIONS.md ("Rollback") for restoring the previous app version in full.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.ERICK_PORT || 8080);
const users = new Map(), byPhone = new Map(), otps = new Map(), tokens = new Map(), rides = new Map(), rickshaws = new Map();
const landmarks = [
  ['Main Gate', 28.6139, 77.2090], ['Central Library', 28.6145, 77.2105], ['Hostel Block A', 28.6155, 77.2118],
  ['Hostel Block B', 28.6162, 77.2125], ['Girls Hostel', 28.6150, 77.2065], ['Cafeteria & Canteen', 28.6132, 77.2100],
  ['Academic Block 1', 28.6122, 77.2082], ['Sports Complex', 28.6168, 77.2085]
];
const id = () => crypto.randomUUID();
const json = (res, status, data) => { const body = JSON.stringify(data); res.writeHead(status, {'content-type':'application/json', 'content-length':Buffer.byteLength(body)}); res.end(body); };
const getBody = req => new Promise(resolve => { let text=''; req.on('data', chunk => text += chunk); req.on('end', () => { try { resolve(JSON.parse(text || '{}')); } catch { resolve({}); } }); });
const userFor = req => users.get(tokens.get((req.headers.authorization || '').replace('Bearer ', '')));

for (let n = 1; n <= 10; n++) {
  const uid=id(), point=landmarks[n % landmarks.length], name=`Demo Driver ${n}`;
  const user={id:uid, phone:`90000000${String(n).padStart(2,'0')}`, username:`demo_driver_${n}`, name, role:'driver', is_verified:true};
  users.set(uid,user); byPhone.set(user.phone,uid); rickshaws.set(uid,{vehicle_number:`DL 01 ER ${1200+n}`,is_active:false,lat:point[1],lng:point[2],capacity:4});
}
// A small realistic data set makes the local demo useful immediately. These
// records live only while the local development host is running.
for (let n = 1; n <= 15; n++) {
  const uid=id(), phone=`80000000${String(n).padStart(2,'0')}`;
  const user={id:uid,phone,username:`demo_passenger_${n}`,name:`Demo Passenger ${n}`,role:'passenger',is_verified:true};
  users.set(uid,user); byPhone.set(phone,uid);
  if (n <= 8) {
    const pickup=landmarks[n % landmarks.length], dropoff=landmarks[(n+3) % landmarks.length];
    const driverId=[...rickshaws.keys()][n % rickshaws.size];
    const ride={id:id(),passenger_id:uid,pickup_name:pickup[0],pickup_lat:pickup[1],pickup_lng:pickup[2],dropoff_name:dropoff[0],dropoff_lat:dropoff[1],dropoff_lng:dropoff[2],status:'completed',driver_id:driverId,completed_at:Date.now()-n*86400000,estimated_fare:10};
    rides.set(ride.id,ride);
  }
}
function staticFile(res, pathname) {
  let target = pathname === '/' ? '/passenger/' : pathname;
  if (target === '/passenger' || target.startsWith('/passenger/')) target = `/passenger-app/${target.slice('/passenger'.length)}`;
  if (target === '/driver' || target.startsWith('/driver/')) target = `/driver-app/${target.slice('/driver'.length)}`;
  if (target.endsWith('/')) target += 'index.html';
  const file = path.resolve(root, `.${target}`);
  // Only the two app folders are public; previously any repository file (including backend/.env) was served.
  const allowed = ['passenger-app', 'driver-app'].some(dir => file.startsWith(path.join(root, dir) + path.sep));
  if (!allowed || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return json(res,404,{detail:'Not found'});
  const type = {'.html':'text/html; charset=utf-8','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)] || 'application/octet-stream';
  res.writeHead(200, {'content-type':type}); fs.createReadStream(file).pipe(res);
}
const server = http.createServer(async (req,res) => {
  const url = new URL(req.url, `http://${req.headers.host}`), pathname=url.pathname;
  if (req.method === 'GET' && pathname === '/api/health') return json(res,200,{status:'ok', runtime:'node', api:'ERICK development host'});
  if (req.method === 'GET' && pathname === '/api/locations/landmarks') return json(res,200,landmarks.map(([name,lat,lng])=>({name,lat,lng})));
  const user = userFor(req);
  if (req.method === 'POST' && pathname === '/api/auth/request-otp') {
    const body=await getBody(req), phone=String(body.phone||'').replace(/[ -]/g,'');
    if (!/^\+?\d{10,15}$/.test(phone)) return json(res,422,{detail:'Enter a valid 10 to 15 digit phone number'});
    const otp=String(Math.floor(100000+Math.random()*900000)); otps.set(phone,{otp,role:body.role||'passenger',expires:Date.now()+300000});
    return json(res,200,{message:'OTP sent',debug_otp:otp,expires_in:300,resend_in:30});
  }
  if (req.method === 'POST' && pathname === '/api/auth/register') {
    const body=await getBody(req), phone=String(body.phone||'').replace(/[ -]/g,''), username=String(body.username||'').trim();
    if (username.length < 2 || username.length > 60) return json(res,422,{detail:'Enter a name between 2 and 60 characters'});
    if (!/^\+?\d{10,15}$/.test(phone)) return json(res,422,{detail:'Enter a valid 10 to 15 digit phone number'});
    if (byPhone.has(phone)) return json(res,409,{detail:'That phone number is already registered'});
    const uid=id(); const profile={id:uid,phone,username,name:username,role:body.role==='driver'?'driver':'passenger',is_verified:false};
    users.set(uid,profile); byPhone.set(phone,uid); return json(res,201,{message:'Account created. Verify your phone to continue.'});
  }
  if (req.method === 'POST' && pathname === '/api/auth/verify-otp') {
    const body=await getBody(req), phone=String(body.phone||'').replace(/[ -]/g,''), record=otps.get(phone);
    if (!record || record.otp !== String(body.otp||'') || Date.now()>record.expires) return json(res,400,{detail:'Invalid or expired OTP'});
    otps.delete(phone); let uid=byPhone.get(phone), profile=users.get(uid);
    if (!profile) { uid=id(); profile={id:uid,phone,username:`${body.role||record.role}_${phone.slice(-4)}`,name:null,role:body.role||record.role,is_verified:true}; users.set(uid,profile);byPhone.set(phone,uid); }
    const token=id();tokens.set(token,uid);return json(res,200,{access_token:token,token,token_type:'bearer'});
  }
  if (!pathname.startsWith('/api/')) return staticFile(res,pathname);
  if (!user) return json(res,401,{detail:'Login required'});
  if (req.method === 'GET' && pathname === '/api/auth/me') return json(res,200,user);
  if ((req.method === 'PUT'||req.method==='POST') && pathname === '/api/auth/profile') { const body=await getBody(req); user.name=String(body.name||'').trim();return json(res,200,user); }
  if (req.method === 'POST' && pathname === '/api/passenger/ride-request') {
    if(user.role!=='passenger')return json(res,403,{detail:'Passenger login required'}); const b=await getBody(req), ride={id:id(),passenger_id:user.id,pickup_name:b.pickup_name||'Pickup',pickup_lat:b.pickup_lat,pickup_lng:b.pickup_lng,dropoff_name:b.dropoff_name||'Drop-off',dropoff_lat:b.dropoff_lat,dropoff_lng:b.dropoff_lng,status:'queued',driver_id:null,created_at:Date.now(),estimated_fare:10};rides.set(ride.id,ride);return json(res,201,{request_id:ride.id,status:'queued',estimated_fare:10,simulation:true});
  }
  if (req.method === 'GET' && pathname === '/api/passenger/ride-history') {
    if(user.role!=='passenger') return json(res,403,{detail:'Passenger login required'});
    return json(res,200,[...rides.values()].filter(r=>r.passenger_id===user.id).sort((a,b)=>(b.completed_at||b.created_at||0)-(a.completed_at||a.created_at||0)).map(r=>({request_id:r.id,pickup_name:r.pickup_name,dropoff_name:r.dropoff_name,status:r.status,fare:r.estimated_fare||10,completed_at:r.completed_at||null})));
  }
  if (req.method === 'GET' && pathname.startsWith('/api/passenger/ride-status/')) { const ride=rides.get(pathname.split('/').at(-1));if(!ride||ride.passenger_id!==user.id)return json(res,404,{detail:'Ride not found'});const driver=users.get(ride.driver_id),rick=rickshaws.get(ride.driver_id);return json(res,200,{request_id:ride.id,status:ride.status,driver_name:driver?.name,driver_rating:4.7,vehicle_number:rick?.vehicle_number,eta_minutes:ride.status==='matched'||ride.status==='pickup_enroute'?3:0,driver_location:rick&&{lat:rick.lat,lng:rick.lng},simulation:true}); }
  if (req.method === 'POST' && pathname.startsWith('/api/passenger/cancel/')) { const ride=rides.get(pathname.split('/').at(-1));if(ride&&ride.passenger_id===user.id)ride.status='cancelled';return json(res,200,{message:'Ride cancelled'}); }
  if (req.method === 'GET' && pathname === '/api/driver/my-rickshaw') { const r=rickshaws.get(user.id);return json(res,200,{registered:!!r,rickshaw:r||null}); }
  if (req.method === 'POST' && pathname === '/api/driver/register-rickshaw') { const b=await getBody(req);rickshaws.set(user.id,{vehicle_number:b.vehicle_number||'DEMO 001',is_active:false,lat:28.6139,lng:77.2090,capacity:4});return json(res,200,{message:'Rickshaw registered successfully'}); }
  if (req.method === 'POST' && (pathname.endsWith('/go-online')||pathname.endsWith('/go-offline'))) { const r=rickshaws.get(user.id);if(r)r.is_active=pathname.endsWith('/go-online');return json(res,200,{message:'Availability updated'}); }
  if ((req.method === 'POST' || req.method === 'PUT') && pathname === '/api/driver/settings') { const b=await getBody(req), r=rickshaws.get(user.id); if (String(b.name||'').trim()) user.name=String(b.name).trim(); if (r && String(b.vehicle_number||'').trim()) r.vehicle_number=String(b.vehicle_number).trim(); if (r && Number.isFinite(Number(b.max_detour_meters))) r.max_detour_meters=Number(b.max_detour_meters); return json(res,200,{message:'Settings saved',user,rickshaw:r}); }
  if (req.method === 'GET' && pathname === '/api/driver/nearby-requests') { if(user.role!=='driver')return json(res,403,{detail:'Driver login required'});return json(res,200,{simulation:true,requests:[...rides.values()].filter(r=>['pending','queued'].includes(r.status)).map(r=>({request_id:r.id,passenger_name:users.get(r.passenger_id)?.name||'Passenger',pickup:{name:r.pickup_name,lat:r.pickup_lat,lng:r.pickup_lng},dropoff:{name:r.dropoff_name,lat:r.dropoff_lat,lng:r.dropoff_lng},distance_to_pickup_meters:300,estimated_fare:10}))}); }
  if (req.method === 'POST' && pathname.startsWith('/api/driver/accept-passenger/')) { const ride=rides.get(pathname.split('/').at(-1));if(!ride||!['pending','queued'].includes(ride.status))return json(res,409,{detail:'This ride was just accepted by another driver'});ride.status='matched';ride.driver_id=user.id;ride.accepted_at=Date.now();return json(res,200,{message:'Accepted passenger',ride_id:ride.id}); }
  if (req.method === 'POST' && pathname.startsWith('/api/driver/reject-passenger/')) { return json(res,200,{message:'Request dismissed'}); }
  if (req.method === 'POST' && (pathname.startsWith('/api/driver/pickup/')||pathname.startsWith('/api/driver/dropoff/'))) { const ride=rides.get(pathname.split('/').at(-1));if(ride&&ride.driver_id===user.id)ride.status=pathname.includes('/pickup/')?'picked_up':'completed';return json(res,200,{message:'Ride updated'}); }
  if (req.method === 'GET' && pathname === '/api/driver/active-ride') {
    const ride=[...rides.values()].find(r=>r.driver_id===user.id && ['matched','pickup_enroute','picked_up'].includes(r.status));
    if (!ride) return json(res,200,{active_ride:null,passengers:[],stops:[],next_stop:null});
    const passenger=users.get(ride.passenger_id), inVehicle=ride.status==='picked_up';
    const passengerData={request_id:ride.id,name:passenger?.name||'Passenger',pickup_name:ride.pickup_name,dropoff_name:ride.dropoff_name,status:inVehicle?'in_vehicle':'waiting_pickup'};
    const next={request_id:ride.id,type:inVehicle?'dropoff':'pickup',name:inVehicle?ride.dropoff_name:ride.pickup_name,passenger_name:passengerData.name,order:1,lat:inVehicle?ride.dropoff_lat:ride.pickup_lat,lng:inVehicle?ride.dropoff_lng:ride.pickup_lng};
    return json(res,200,{active_ride:{ride_id:ride.id,route_polyline:[[ride.pickup_lat,ride.pickup_lng],[ride.dropoff_lat,ride.dropoff_lng]]},passengers:[passengerData],stops:[next],next_stop:next});
  }
  return json(res,404,{detail:'Not found'});
});
server.on('error', error => {
  console.error(`ERICK failed to bind 127.0.0.1:${port}: ${error.code || error.message}`);
  process.exitCode = 1;
});
server.listen(port,'127.0.0.1',() => {
  console.warn('WARNING: server.mjs is deprecated and incompatible with the current apps. Use start-erick.cmd (FastAPI).');
  console.log(`ERICK Node development host listening at http://127.0.0.1:${port}/passenger/`);
  http.get(`http://127.0.0.1:${port}/api/health`, response => console.log(`Self-check: HTTP ${response.statusCode}`)).on('error', error => console.error(`Self-check failed: ${error.message}`));
});
