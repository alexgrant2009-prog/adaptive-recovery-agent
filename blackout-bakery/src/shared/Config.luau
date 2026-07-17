--!strict
-- Config (ReplicatedStorage) -------------------------------------------------
-- Client-VISIBLE constants only. Anything here can be read by any client from
-- the dev console, so NOTHING secret (no recipe quantities) may live in this
-- module. The secret recipe data lives in ServerStorage/RecipeConfig, which is
-- never replicated to clients.
-------------------------------------------------------------------------------

local Config = {}

-- Names of the RemoteEvents the server creates under
-- ReplicatedStorage/BlackoutBakery/Remotes at boot.
Config.Remotes = {
	Ticket = "TicketEvent", -- server -> Reader only
	Taste = "TasteEvent", -- server -> Taster only
	HUD = "HUDEvent", -- server -> everyone (rep / timer / score)
	Role = "RoleEvent", -- server -> each player (their own role)
	Round = "RoundEvent", -- server -> everyone (lobby / start / summary)
	Bowl = "BowlEvent", -- server -> Cooks only (what's in the bowl so far)
}

-- Roles a player can hold in a round.
Config.Roles = {
	Reader = "Reader",
	Cook = "Cook",
	Taster = "Taster",
}

-- Ingredient identities are NOT secret -- the bins physically exist in the
-- world, so their names are readable by anyone. Only the per-recipe QUANTITIES
-- are secret. Order here also drives bin layout in WorldBuilder.
Config.Ingredients = {
	"Flour",
	"Sugar",
	"Butter",
	"Eggs",
	"ChocolateChips",
	"Milk",
	"Salt",
	"Vanilla",
}

-- Timings & scoring (tunable).
Config.Timing = {
	RoundSeconds = 180, -- shift length per round
	OrderPatienceSeconds = 45, -- base patience per order (harder = shorter)
	IntermissionSeconds = 8, -- between rounds (role rotation + summary)
	MinPlayersToStart = 2,
	HudTickSeconds = 0.25, -- how often the server pushes timer updates
}

Config.Scoring = {
	StartingReputation = 5,
	ReputationLossPerMiss = 1,
	ReputationLossPerWrong = 1,
	ScorePerCorrectDish = 100,
	SpeedBonusPerSecondLeft = 2,
}

-- Whether the Cook's bins carry a readable label. Flip to false for
-- "learn-the-layout-from-memory" hard mode (see spec "things to decide").
Config.LabelBins = true

return Config
