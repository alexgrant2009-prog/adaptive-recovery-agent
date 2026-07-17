--!strict
-- SubmitStation (server) ----------------------------------------------------
-- The serve counter. On the Cook's "Serve" prompt it compares the final dish
-- (DishState) against the secret target recipe, scores it, and reports the
-- result to the controller, which docks/awards reputation and queues the next
-- order. All comparison is server-side against ServerStorage data.
-------------------------------------------------------------------------------

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Config = require(ReplicatedStorage.BlackoutBakery.Config)
local Net = require(script.Parent.Parent.modules.Net)
local RoleManager = require(script.Parent.Parent.modules.RoleManager)
local DishState = require(script.Parent.Parent.modules.DishState)
local OrderManager = require(script.Parent.Parent.modules.OrderManager)

local SubmitStation = {}

export type Result = {
	correct: boolean,
	scoreDelta: number,
	recipeName: string,
}

-- Exact-match check: every required ingredient present in the exact quantity,
-- and nothing extra in the bowl.
local function matches(snapshot: { [string]: number }, targets: { [string]: number }): boolean
	for ingredient, needed in targets do
		if (snapshot[ingredient] or 0) ~= needed then
			return false
		end
	end
	for ingredient, amount in snapshot do
		if amount > 0 and (targets[ingredient] == nil or targets[ingredient] == 0) then
			return false
		end
	end
	return true
end

-- `resolve` is called with a Result after a valid submit; the controller reacts
-- (score, reputation, spawn next). Returns nothing.
function SubmitStation.bind(submitPrompt: ProximityPrompt, resolve: (player: Player, result: Result) -> ())
	submitPrompt.Triggered:Connect(function(player: Player)
		if not RoleManager.isCook(player) then
			return
		end

		local recipe = OrderManager.getCurrentRecipe()
		if not recipe then
			return -- nothing to serve right now
		end

		-- Must actually be baked before it can be served.
		if not DishState.isInOven() then
			Net.get(Config.Remotes.HUD):FireClient(player, { toast = "Bake it before you serve it!" })
			return
		end

		local correct = matches(DishState.snapshot(), recipe.ingredients)
		local scoreDelta = 0
		if correct then
			local speedBonus = math.floor(OrderManager.getPatienceLeft()) * Config.Scoring.SpeedBonusPerSecondLeft
			scoreDelta = Config.Scoring.ScorePerCorrectDish + speedBonus
		end

		DishState.clear()
		resolve(player, {
			correct = correct,
			scoreDelta = scoreDelta,
			recipeName = recipe.name,
		})
	end)
end

return SubmitStation
