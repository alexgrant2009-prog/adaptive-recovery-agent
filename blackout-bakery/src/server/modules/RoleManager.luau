--!strict
-- RoleManager (server) ------------------------------------------------------
-- Assigns Reader / Cook / Taster at round start and rotates them every round so
-- everyone plays all three across a session. All role queries in the game go
-- through here, and the stations trust ONLY this module (never the client).
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)

local Roles = Config.Roles

local RoleManager = {}

-- Which slots exist for a given player count. 4th player = second Cook.
-- With only 2 players there is no dedicated Taster, so the Reader also gets the
-- Taste prompt (they have their hands free anyway).
local function slotsFor(count: number): { string }
	if count <= 2 then
		return { Roles.Reader, Roles.Cook }
	elseif count == 3 then
		return { Roles.Reader, Roles.Cook, Roles.Taster }
	else
		return { Roles.Reader, Roles.Cook, Roles.Cook, Roles.Taster }
	end
end

local assignments: { [Player]: string } = {}
local readerPlayer: Player? = nil
local cooks: { Player } = {}
local tasters: { Player } = {}

-- `roundIndex` starts at 0 and increments each round; it rotates the mapping so
-- players cycle through the slots.
function RoleManager.assign(players: { Player }, roundIndex: number)
	table.clear(assignments)
	readerPlayer = nil
	table.clear(cooks)
	table.clear(tasters)

	local count = #players
	if count == 0 then
		return
	end

	local slots = slotsFor(count)
	local offset = roundIndex % count

	for i, player in players do
		-- Rotate: the same player lands in a different slot each round.
		local slotIndex = ((i - 1 + offset) % count) + 1
		local role = slots[slotIndex]
		assignments[player] = role

		if role == Roles.Reader then
			readerPlayer = player
		elseif role == Roles.Cook then
			table.insert(cooks, player)
		elseif role == Roles.Taster then
			table.insert(tasters, player)
		end
	end

	-- 2-player fallback: Reader doubles as Taster.
	if #tasters == 0 and readerPlayer then
		table.insert(tasters, readerPlayer)
	end
end

function RoleManager.roleOf(player: Player): string?
	return assignments[player]
end

function RoleManager.isCook(player: Player): boolean
	return assignments[player] == Roles.Cook
end

function RoleManager.isTaster(player: Player): boolean
	for _, p in tasters do
		if p == player then
			return true
		end
	end
	return false
end

function RoleManager.getReader(): Player?
	return readerPlayer
end

function RoleManager.getTasters(): { Player }
	return tasters
end

function RoleManager.getAssignments(): { [Player]: string }
	return assignments
end

return RoleManager
