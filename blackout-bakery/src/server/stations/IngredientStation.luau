--!strict
-- IngredientStation (server) ------------------------------------------------
-- Owns every ProximityPrompt a Cook uses: ingredient bins, the oven, and the
-- trash. Each .Triggered fires ON THE SERVER, and we check the player's role
-- here before touching DishState. This is the real gate:
--
--   * The client also disables these prompts locally for non-Cooks (feel), but
--     that is cosmetic. Even a hacker who re-enables the prompt and triggers it
--     gets rejected here, because the server never trusts the client's role.
--
-- The Taste prompt on the bowl is also wired here but routed to TasteService.
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local Net = require(script.Parent.Parent.modules.Net)
local RoleManager = require(script.Parent.Parent.modules.RoleManager)
local DishState = require(script.Parent.Parent.modules.DishState)
local TasteService = require(script.Parent.Parent.modules.TasteService)
local OrderManager = require(script.Parent.Parent.modules.OrderManager)

local IngredientStation = {}

-- Pushes the current bowl contents to every Cook so they can see what they've
-- added. Bowl contents are not secret (the Cooks put them there), unlike the
-- recipe, so this broadcast to Cooks is safe.
local function pushBowlToCooks()
	local snapshot = DishState.snapshot()
	for player, role in RoleManager.getAssignments() do
		if role == Config.Roles.Cook then
			Net.get(Config.Remotes.Bowl):FireClient(player, {
				contents = snapshot,
				inOven = DishState.isInOven(),
			})
		end
	end
end

IngredientStation.pushBowlToCooks = pushBowlToCooks

-- Wire up the prompts produced by WorldBuilder. `prompts` is:
--   { ingredients = {[name]=ProximityPrompt}, oven, trash, taste }
function IngredientStation.bind(prompts: any)
	-- Ingredient bins: Cook-only, blocked once the dish is in the oven.
	for ingredient, prompt in prompts.ingredients do
		(prompt :: ProximityPrompt).Triggered:Connect(function(player: Player)
			if not RoleManager.isCook(player) then
				return
			end
			if DishState.isInOven() then
				return -- already baking; trash it to start over
			end
			DishState.add(ingredient)
			pushBowlToCooks()
		end)
	end

	-- Oven: Cook-only. Baking locks the bowl so it's ready to serve.
	if prompts.oven then
		(prompts.oven :: ProximityPrompt).Triggered:Connect(function(player: Player)
			if not RoleManager.isCook(player) then
				return
			end
			if DishState.isEmpty() then
				return
			end
			DishState.setInOven(true)
			pushBowlToCooks()
		end)
	end

	-- Trash: Cook-only. Soft-fail reset -- dump the bowl and start over. The
	-- cost is the time you burn, which keeps the panic up (see spec: chose
	-- soft-fail-via-reset over an instant hard fail).
	if prompts.trash then
		(prompts.trash :: ProximityPrompt).Triggered:Connect(function(player: Player)
			if not RoleManager.isCook(player) then
				return
			end
			DishState.clear()
			pushBowlToCooks()
		end)
	end

	-- Taste: Taster-only (Reader too in 2-player games). Routed to TasteService,
	-- which validates the role again and fires the hint to Tasters only.
	if prompts.taste then
		(prompts.taste :: ProximityPrompt).Triggered:Connect(function(player: Player)
			TasteService.handleTaste(player, OrderManager.getTargets())
		end)
	end
end

return IngredientStation
