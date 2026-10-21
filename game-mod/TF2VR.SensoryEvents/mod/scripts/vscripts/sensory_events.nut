// TF2VR.SensoryEvents - client side event feed for local sensory bridges.
// Everything here only reads game state; it never changes gameplay.
// Events are batched and POSTed at most ~30 times a second to:
//   127.0.0.1:47770  bHaptics bridge   (TF2VR bHaptics)
//   127.0.0.1:47771  Omara scent bridge (TF2VR Omara)
// A bridge that is not running is retried with a growing back-off.

global function TFSE_Init
global function TFSE_PilotDamage
global function TFSE_TitanDamage

const string TFSE_URL_HAPTICS = "http://127.0.0.1:47770/e"
const string TFSE_URL_SCENT = "http://127.0.0.1:47771/e"
const float TFSE_FLUSH_INTERVAL = 0.03
const float TFSE_STATE_INTERVAL = 0.1
const int TFSE_MAX_QUEUE = 64
const float TFSE_RETRY_MIN = 5.0
const float TFSE_RETRY_MAX = 30.0

struct
{
	array<string> queue
	float lastFlush = 0.0
	table<int, string> sourceNames

	bool hapticsDown = false
	float hapticsRetryAt = 0.0
	float hapticsBackoff = 5.0

	bool scentDown = false
	float scentRetryAt = 0.0
	float scentBackoff = 5.0
} file

void function TFSE_Init()
{
	table sources = expect table( getconsttable().eDamageSourceId )
	foreach ( name, id in sources )
		file.sourceNames[ expect int( id ) ] <- expect string( name )

	thread TFSE_Loop()
}

// ---------------------------------------------------------------- damage
// Called from the hook lines the installer adds to cl_damage_indicator.gnut.

void function TFSE_PilotDamage( float damage, vector origin, int damageType, int sourceId )
{
	TFSE_QueueHit( "hit", damage, origin, damageType, sourceId, false )
}

void function TFSE_TitanDamage( float damage, vector origin, int damageType, int sourceId, bool doomedNow )
{
	TFSE_QueueHit( "thit", damage, origin, damageType, sourceId, doomedNow )
}

void function TFSE_QueueHit( string kindOfHit, float damage, vector origin, int damageType, int sourceId, bool doomedNow )
{
	entity player = GetLocalViewPlayer()
	if ( !IsValid( player ) )
		return

	string kind = "other"
	if ( damageType & DF_MELEE )
		kind = "melee"
	else if ( damageType & DF_EXPLOSION )
		kind = "explosion"
	else if ( damageType & DF_ELECTRICAL )
		kind = "electric"
	else if ( damageType & DF_SHOTGUN )
		kind = "shotgun"
	else if ( damageType & DF_BULLET )
		kind = "bullet"

	string src = ( sourceId in file.sourceNames ) ? file.sourceNames[ sourceId ] : ""
	bool head = ( damageType & DF_HEADSHOT ) ? true : false
	bool crit = ( damageType & DF_CRITICAL ) ? true : false
	bool shield = ( damageType & DF_SHIELD_DAMAGE ) ? true : false

	TFSE_Queue( format( "{\"e\":\"%s\",\"dmg\":%.1f,\"kind\":\"%s\",\"src\":\"%s\",\"sid\":%d,\"o\":%s,\"p\":%s,\"yaw\":%.1f,\"head\":%s,\"crit\":%s,\"shield\":%s,\"doomed\":%s}",
		kindOfHit, damage, kind, src, sourceId,
		TFSE_Vec( origin ), TFSE_Vec( player.CameraPosition() ), player.CameraAngles().y,
		TFSE_Bool( head ), TFSE_Bool( crit ), TFSE_Bool( shield ), TFSE_Bool( doomedNow ) ) )
}

// ---------------------------------------------------------------- state loop

void function TFSE_Loop()
{
	bool wasAlive = false
	bool wasTitan = false
	bool wasGround = true
	bool wasWall = false
	bool wasSlide = false
	float lastVz = 0.0
	float lastHSpeed = 0.0
	float lastState = 0.0
	entity lastWeapon = null
	int lastClip = -1
	bool pendingDisembark = false
	float disembarkAt = 0.0

	TFSE_Queue( format( "{\"e\":\"map\",\"map\":\"%s\"}", GetMapName() ) )

	while ( true )
	{
		WaitFrame()
		TFSE_Flush()

		entity player = GetLocalViewPlayer()
		if ( !IsValid( player ) || IsWatchingReplay() )
			continue

		float now = Time()
		bool alive = IsAlive( player )
		if ( alive != wasAlive )
		{
			TFSE_Queue( alive ? "{\"e\":\"spawn\"}" : "{\"e\":\"death\"}" )
			wasAlive = alive
			lastWeapon = null
		}
		if ( !alive )
			continue

		vector vel = player.GetVelocity()
		float hspeed = sqrt( vel.x * vel.x + vel.y * vel.y )
		bool titan = player.IsTitan()

		// embark / disembark / eject
		if ( titan != wasTitan )
		{
			if ( titan )
			{
				TFSE_Queue( "{\"e\":\"embark\"}" )
				pendingDisembark = false
			}
			else
			{
				pendingDisembark = true
				disembarkAt = now
			}
			wasTitan = titan
			lastWeapon = null
		}
		if ( pendingDisembark )
		{
			if ( vel.z > 500.0 )
			{
				TFSE_Queue( "{\"e\":\"eject\"}" )
				pendingDisembark = false
			}
			else if ( now - disembarkAt > 0.6 )
			{
				TFSE_Queue( "{\"e\":\"disembark\"}" )
				pendingDisembark = false
			}
		}

		// movement
		bool ground = player.IsOnGround()
		bool wall = !titan && player.IsWallRunning()
		bool slide = !titan && player.IsSliding()

		if ( !titan )
		{
			if ( !ground && wasGround && vel.z > 150.0 )
				TFSE_Queue( "{\"e\":\"jump\"}" )
			else if ( !ground && !wasGround && !wall && vel.z - lastVz > 250.0 )
				TFSE_Queue( wasWall ? "{\"e\":\"walljump\"}" : "{\"e\":\"djump\"}" )
		}
		if ( ground && !wasGround && lastVz < -200.0 )
			TFSE_Queue( format( "{\"e\":\"land\",\"spd\":%.0f,\"titan\":%s}", -lastVz, TFSE_Bool( titan ) ) )
		if ( wall != wasWall )
			TFSE_Queue( wall ? "{\"e\":\"wall_on\"}" : "{\"e\":\"wall_off\"}" )
		if ( slide != wasSlide )
			TFSE_Queue( slide ? "{\"e\":\"slide_on\"}" : "{\"e\":\"slide_off\"}" )
		if ( titan && hspeed - lastHSpeed > 300.0 )
			TFSE_Queue( format( "{\"e\":\"tdash\",\"v\":%s,\"yaw\":%.1f}", TFSE_Vec( vel ), player.CameraAngles().y ) )

		wasGround = ground
		wasWall = wall
		wasSlide = slide
		lastVz = vel.z
		lastHSpeed = hspeed

		// weapon fire / reload, read from the clip counter
		entity weapon = player.GetActiveWeapon()
		if ( IsValid( weapon ) )
		{
			int clip = weapon.GetWeaponPrimaryClipCount()
			if ( weapon == lastWeapon && lastClip >= 0 )
			{
				if ( clip < lastClip )
					TFSE_Queue( format( "{\"e\":\"fire\",\"w\":\"%s\",\"n\":%d,\"titan\":%s}", weapon.GetWeaponClassName(), lastClip - clip, TFSE_Bool( titan ) ) )
				else if ( clip > lastClip )
					TFSE_Queue( format( "{\"e\":\"reload\",\"w\":\"%s\",\"n\":%d}", weapon.GetWeaponClassName(), clip - lastClip ) )
			}
			lastWeapon = weapon
			lastClip = clip
		}
		else
		{
			lastWeapon = null
			lastClip = -1
		}

		// continuous state for looping effects and scent ambience
		if ( now - lastState >= TFSE_STATE_INTERVAL )
		{
			lastState = now
			float maxHp = float( player.GetMaxHealth() )
			float hp = maxHp > 0.0 ? float( player.GetHealth() ) / maxHp : 1.0
			TFSE_Queue( format( "{\"e\":\"state\",\"hp\":%.2f,\"titan\":%s,\"ground\":%s,\"wall\":%s,\"slide\":%s,\"spd\":%.0f}",
				hp, TFSE_Bool( titan ), TFSE_Bool( ground ), TFSE_Bool( wall ), TFSE_Bool( slide ), hspeed ) )
		}
	}
}

// ---------------------------------------------------------------- transport

void function TFSE_Queue( string json )
{
	if ( file.queue.len() >= TFSE_MAX_QUEUE )
		file.queue.remove( 0 )
	file.queue.append( json )
}

void function TFSE_Flush()
{
	if ( file.queue.len() == 0 )
		return
	float now = Time()
	if ( now - file.lastFlush < TFSE_FLUSH_INTERVAL )
		return
	file.lastFlush = now

	string body = "["
	foreach ( int i, string ev in file.queue )
	{
		if ( i > 0 )
			body += ","
		body += ev
	}
	body += "]"
	file.queue.clear()

	bool sentAny = false
	if ( !file.hapticsDown || now >= file.hapticsRetryAt )
	{
		NSHttpPostBody( TFSE_URL_HAPTICS, body, TFSE_HapticsOk, TFSE_HapticsFail )
		sentAny = true
	}
	if ( !file.scentDown || now >= file.scentRetryAt )
	{
		NSHttpPostBody( TFSE_URL_SCENT, body, TFSE_ScentOk, TFSE_ScentFail )
		sentAny = true
	}

	// Fallback for when HTTP is unavailable (no -allowlocalhttp): bridges can
	// also read these lines from the Northstar log.
	if ( !sentAny )
		printt( "[TFSE] " + body )
}

void function TFSE_HapticsOk( HttpRequestResponse response )
{
	file.hapticsDown = false
	file.hapticsBackoff = TFSE_RETRY_MIN
}

void function TFSE_HapticsFail( HttpRequestFailure failure )
{
	if ( !file.hapticsDown )
		printt( "[TFSE] bHaptics bridge not reachable (" + failure.errorMessage + ")" )
	file.hapticsDown = true
	file.hapticsRetryAt = Time() + file.hapticsBackoff
	file.hapticsBackoff = min( file.hapticsBackoff * 2.0, TFSE_RETRY_MAX )
}

void function TFSE_ScentOk( HttpRequestResponse response )
{
	file.scentDown = false
	file.scentBackoff = TFSE_RETRY_MIN
}

void function TFSE_ScentFail( HttpRequestFailure failure )
{
	if ( !file.scentDown )
		printt( "[TFSE] Omara bridge not reachable (" + failure.errorMessage + ")" )
	file.scentDown = true
	file.scentRetryAt = Time() + file.scentBackoff
	file.scentBackoff = min( file.scentBackoff * 2.0, TFSE_RETRY_MAX )
}

// ---------------------------------------------------------------- helpers

string function TFSE_Vec( vector v )
{
	return format( "[%.1f,%.1f,%.1f]", v.x, v.y, v.z )
}

string function TFSE_Bool( bool b )
{
	return b ? "true" : "false"
}
