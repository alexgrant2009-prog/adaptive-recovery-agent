--!strict
-- OrderManager (server) -----------------------------------------------------
-- Picks a recipe, tracks its patience timer, and fires the ticket to the Reader
-- ONLY. This is the heart of the security model:
--
--   ticketEvent:FireClient(readerPlayer, ...)   -- Reader, and no one else
--
-- The Cook and Taster clients never have this event fired at them, so the
-- recipe simply never arrives on their machines. There is no client-side
-- "hide the frame" check to bypass -- the data isn't there to find.
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local Net = require(script.Parent.Net)
local RoleManager = require(script.Parent.RoleManager)
local RecipeConfig = require(ServerStorage.BlackoutBakery.RecipeConfig)

type Recipe = RecipeConfig.Recipe

local OrderManager = {}

local currentRecipe: Recipe? = nil
local patienceLeft = 0
local patienceMax = 0
local sinceTicketPush = 0
local servedCount = 0

local TICKET_PUSH_INTERVAL = 1.0 -- re-send the Reader's countdown once a second

-- Patience shrinks as the shift heats up.
local function patienceFor(tier: number): number
	local base = Config.Timing.OrderPatienceSeconds
	return math.max(18, base - (tier - 1) * 8)
end

-- Sends the full ticket to the Reader only. Safe to call when there is no
-- Reader yet (mid-rotation) -- it just no-ops.
local function fireTicketToReader()
	local reader = RoleManager.getReader()
	if not reader or not currentRecipe then
		return
	end
	Net.get(Config.Remotes.Ticket):FireClient(reader, {
		recipeId = currentRecipe.id,
		name = currentRecipe.name,
		lines = RecipeConfig.toTicketLines(currentRecipe),
		timeLeft = math.floor(patienceLeft),
		timeMax = patienceMax,
	})
end

-- Lighter update used every second: just the countdown, still Reader-only.
local function pushTicketTime()
	local reader = RoleManager.getReader()
	if not reader or not currentRecipe then
		return
	end
	Net.get(Config.Remotes.Ticket):FireClient(reader, {
		recipeId = currentRecipe.id,
		timeLeft = math.floor(patienceLeft),
	})
end

function OrderManager.reset()
	currentRecipe = nil
	patienceLeft = 0
	servedCount = 0
end

function OrderManager.getCurrentRecipe(): Recipe?
	return currentRecipe
end

function OrderManager.getTargets(): { [string]: number }?
	return if currentRecipe then currentRecipe.ingredients else nil
end

function OrderManager.getPatienceLeft(): number
	return patienceLeft
end

function OrderManager.ordersServed(): number
	return servedCount
end

-- Chooses the next recipe (difficulty scales with how far into the shift we are)
-- and pushes the ticket to the Reader. `maxTier` is decided by the controller.
function OrderManager.spawnNext(maxTier: number)
	local pool = RecipeConfig.pool(maxTier)
	currentRecipe = pool[math.random(1, #pool)]
	patienceMax = patienceFor((currentRecipe :: Recipe).tier)
	patienceLeft = patienceMax
	sinceTicketPush = 0
	fireTicketToReader()
end

-- Clears the ticket from the Reader's screen (order resolved).
local function clearReaderTicket()
	local reader = RoleManager.getReader()
	if reader then
		Net.get(Config.Remotes.Ticket):FireClient(reader, { cleared = true })
	end
end

-- Marks the current order resolved (correct or wrong) and clears the ticket.
function OrderManager.completeCurrent()
	servedCount += 1
	currentRecipe = nil
	clearReaderTicket()
end

-- Re-fires the current ticket to whoever the Reader is now (used right after a
-- role rotation so the new Reader immediately sees the live order).
function OrderManager.refreshReader()
	fireTicketToReader()
end

-- Advances the patience clock. Returns true exactly once, on the tick the order
-- runs out of patience, so the controller can dock reputation and move on.
function OrderManager.tick(dt: number): boolean
	if not currentRecipe then
		return false
	end
	patienceLeft -= dt
	sinceTicketPush += dt

	if patienceLeft <= 0 then
		patienceLeft = 0
		return true -- expired this tick
	end

	if sinceTicketPush >= TICKET_PUSH_INTERVAL then
		sinceTicketPush = 0
		pushTicketTime()
	end
	return false
end

return OrderManager
