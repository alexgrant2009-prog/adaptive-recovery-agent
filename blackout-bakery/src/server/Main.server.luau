--!strict
-- Main (server) -------------------------------------------------------------
-- The game controller. Boots networking, builds the world, wires the stations,
-- and runs the lobby -> shift -> summary loop: assigns & rotates roles, spawns
-- orders, ticks patience + the shift clock, and tracks reputation and score.
-------------------------------------------------------------------------------

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local Net = require(script.Parent.modules.Net)
local WorldBuilder = require(script.Parent.modules.WorldBuilder)
local RoleManager = require(script.Parent.modules.RoleManager)
local OrderManager = require(script.Parent.modules.OrderManager)
local DishState = require(script.Parent.modules.DishState)
local IngredientStation = require(script.Parent.stations.IngredientStation)
local SubmitStation = require(script.Parent.stations.SubmitStation)

Net.init()
local prompts = WorldBuilder.build()

-- Session state ------------------------------------------------------------
local reputation = Config.Scoring.StartingReputation
local score = 0
local roundIndex = 0
local inSession = false
local readySet: { [Player]: boolean } = {}

-- Broadcast helpers --------------------------------------------------------
local function broadcastHUD(extra: { [string]: any }?)
	local payload: { [string]: any } = {
		rep = reputation,
		repMax = Config.Scoring.StartingReputation,
		score = score,
	}
	if extra then
		for k, v in extra do
			payload[k] = v
		end
	end
	Net.get(Config.Remotes.HUD):FireAllClients(payload)
end

local function broadcastRound(payload: { [string]: any })
	Net.get(Config.Remotes.Round):FireAllClients(payload)
end

-- Tells each player their role and what they're allowed to do, so the client
-- can enable/disable prompts locally and show the right banner.
local function sendRoles()
	local roleEvent = Net.get(Config.Remotes.Role)
	for player, role in RoleManager.getAssignments() do
		roleEvent:FireClient(player, {
			role = role,
			isReader = role == Config.Roles.Reader,
			canCook = role == Config.Roles.Cook,
			canTaste = RoleManager.isTaster(player),
		})
	end
end

-- Gameplay prompts stay Enabled=false at the instance level (set in
-- WorldBuilder). The CLIENT enables its own prompts locally based on role, so
-- non-cooks never even see them -- and because that's a local-only write, it
-- can't be trusted, which is exactly why the server re-checks the role on every
-- .Triggered. Server never re-enables them globally, avoiding a replication
-- race with the client's local gating.

-- Difficulty ramp: tier 1 early, up to tier 3 as the shift goes on.
local function currentMaxTier(shiftElapsed: number): number
	local frac = shiftElapsed / Config.Timing.RoundSeconds
	if frac > 0.66 then
		return 3
	elseif frac > 0.33 then
		return 2
	end
	return 1
end

-- Station wiring -----------------------------------------------------------
IngredientStation.bind(prompts)

SubmitStation.bind(prompts.submit, function(_player, result)
	if result.correct then
		score += result.scoreDelta
		broadcastHUD({ toast = string.format("%s served! +%d", result.recipeName, result.scoreDelta) })
	else
		reputation -= Config.Scoring.ReputationLossPerWrong
		broadcastHUD({ toast = string.format("Wrong dish! (%s) -1 rep", result.recipeName) })
	end
	OrderManager.completeCurrent()
	-- Next order spawns from the round loop's cadence below.
end)

-- Round loop ---------------------------------------------------------------
local function activePlayers(): { Player }
	return Players:GetPlayers()
end

local function runRound()
	roundIndex += 1
	RoleManager.assign(activePlayers(), roundIndex - 1)
	sendRoles()
	prompts.ready.Enabled = false

	DishState.clear()
	OrderManager.spawnNext(1)
	IngredientStation.pushBowlToCooks()

	broadcastRound({ phase = "round", roundIndex = roundIndex })

	local shiftLeft = Config.Timing.RoundSeconds
	local shiftElapsed = 0
	local sinceHud = 0

	while shiftLeft > 0 and reputation > 0 and inSession do
		local dt = RunService.Heartbeat:Wait()
		shiftLeft -= dt
		shiftElapsed += dt
		sinceHud += dt

		-- Patience clock: docks reputation and rolls to the next order on expiry.
		if OrderManager.tick(dt) then
			reputation -= Config.Scoring.ReputationLossPerMiss
			broadcastHUD({ toast = "Order expired! -1 rep" })
			OrderManager.completeCurrent()
		end

		-- Whenever there's no active order (expired or just served), spawn one.
		if OrderManager.getCurrentRecipe() == nil and reputation > 0 then
			DishState.clear()
			OrderManager.spawnNext(currentMaxTier(shiftElapsed))
			IngredientStation.pushBowlToCooks()
		end

		if sinceHud >= Config.Timing.HudTickSeconds then
			sinceHud = 0
			broadcastHUD({ shiftLeft = math.floor(shiftLeft) })
		end

		-- Not enough players anymore -> bail out to lobby.
		if #activePlayers() < Config.Timing.MinPlayersToStart then
			inSession = false
		end
	end
end

local function runSession()
	inSession = true
	reputation = Config.Scoring.StartingReputation
	score = 0
	roundIndex = 0
	broadcastHUD()

	while inSession and reputation > 0 and #activePlayers() >= Config.Timing.MinPlayersToStart do
		runRound()
		if reputation <= 0 or not inSession then
			break
		end
		-- Intermission between rounds: rotate roles next round, breathe.
		broadcastRound({
			phase = "intermission",
			roundIndex = roundIndex,
			score = score,
			seconds = Config.Timing.IntermissionSeconds,
		})
		local t = Config.Timing.IntermissionSeconds
		while t > 0 and #activePlayers() >= Config.Timing.MinPlayersToStart do
			t -= RunService.Heartbeat:Wait()
		end
	end

	-- Session over: final summary, back to lobby.
	inSession = false
	OrderManager.reset()
	DishState.clear()
	table.clear(readySet)
	broadcastRound({
		phase = "summary",
		score = score,
		rounds = roundIndex,
		reason = if reputation <= 0 then "Reputation hit zero" else "Shift over",
	})
	prompts.ready.Enabled = true
end

-- Lobby / ready-up ---------------------------------------------------------
local function readyCount(): number
	local n = 0
	for _ in readySet do
		n += 1
	end
	return n
end

local function tryStart()
	if inSession then
		return
	end
	local players = activePlayers()
	if #players < Config.Timing.MinPlayersToStart then
		return
	end
	-- Everyone present must be ready.
	for _, p in players do
		if not readySet[p] then
			return
		end
	end
	task.spawn(runSession)
end

prompts.ready.Triggered:Connect(function(player: Player)
	if inSession then
		return
	end
	readySet[player] = true
	broadcastRound({
		phase = "lobby",
		ready = readyCount(),
		need = Config.Timing.MinPlayersToStart,
		total = #activePlayers(),
	})
	tryStart()
end)

Players.PlayerRemoving:Connect(function(player: Player)
	readySet[player] = nil
end)

Players.PlayerAdded:Connect(function()
	if not inSession then
		broadcastRound({
			phase = "lobby",
			ready = readyCount(),
			need = Config.Timing.MinPlayersToStart,
			total = #activePlayers(),
		})
	end
end)

broadcastRound({
	phase = "lobby",
	ready = 0,
	need = Config.Timing.MinPlayersToStart,
	total = #activePlayers(),
})

print("[BlackoutBakery] Server ready. Ready-up at the pad to start a shift.")
