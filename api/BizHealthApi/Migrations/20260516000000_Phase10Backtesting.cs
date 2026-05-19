using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace BizHealthApi.Migrations;

/// <inheritdoc />
public partial class Phase10Backtesting : Migration
{
    /// <inheritdoc />
    protected override void Up(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.CreateTable(
            name: "closed_restaurants",
            columns: table => new
            {
                id = table.Column<Guid>(type: "uuid", nullable: false, defaultValueSql: "gen_random_uuid()"),
                name = table.Column<string>(type: "varchar", nullable: false),
                address = table.Column<string>(type: "varchar", nullable: true),
                city = table.Column<string>(type: "varchar", nullable: true),
                zip = table.Column<string>(type: "varchar", nullable: true),
                google_place_id = table.Column<string>(type: "varchar", nullable: true),
                yelp_id = table.Column<string>(type: "varchar", nullable: true),
                closure_date = table.Column<DateOnly>(type: "date", nullable: true),
                closure_date_estimated = table.Column<bool>(type: "boolean", nullable: true),
                closure_source = table.Column<string>(type: "varchar", nullable: true),
                created_at = table.Column<DateTime>(type: "timestamptz", nullable: false, defaultValueSql: "now()"),
            },
            constraints: table =>
            {
                table.PrimaryKey("PK_closed_restaurants", x => x.id);
            });

        migrationBuilder.CreateTable(
            name: "backtest_cohort",
            columns: table => new
            {
                id = table.Column<Guid>(type: "uuid", nullable: false, defaultValueSql: "gen_random_uuid()"),
                restaurant_id = table.Column<Guid>(type: "uuid", nullable: true),
                cohort_type = table.Column<string>(type: "varchar", nullable: true),
                baseline_score = table.Column<int>(type: "integer", nullable: true),
                baseline_risk_band = table.Column<string>(type: "varchar", nullable: true),
                baseline_date = table.Column<DateOnly>(type: "date", nullable: true),
                baseline_factors = table.Column<string>(type: "jsonb", nullable: true),
                outcome_90d = table.Column<string>(type: "varchar", nullable: true),
                outcome_180d = table.Column<string>(type: "varchar", nullable: true),
                outcome_90d_date = table.Column<DateOnly>(type: "date", nullable: true),
                outcome_180d_date = table.Column<DateOnly>(type: "date", nullable: true),
                closure_date = table.Column<DateOnly>(type: "date", nullable: true),
                closure_source = table.Column<string>(type: "varchar", nullable: true),
                notes = table.Column<string>(type: "text", nullable: true),
                created_at = table.Column<DateTime>(type: "timestamptz", nullable: false, defaultValueSql: "now()"),
            },
            constraints: table =>
            {
                table.PrimaryKey("PK_backtest_cohort", x => x.id);
                table.ForeignKey(
                    name: "FK_backtest_cohort_restaurants_restaurant_id",
                    column: x => x.restaurant_id,
                    principalTable: "restaurants",
                    principalColumn: "id");
            });

        migrationBuilder.CreateIndex(
            name: "idx_closed_restaurants_city",
            table: "closed_restaurants",
            column: "city");

        migrationBuilder.CreateIndex(
            name: "idx_closed_restaurants_closure_date",
            table: "closed_restaurants",
            column: "closure_date");

        migrationBuilder.CreateIndex(
            name: "idx_closed_restaurants_google_id",
            table: "closed_restaurants",
            column: "google_place_id");

        migrationBuilder.CreateIndex(
            name: "idx_backtest_cohort_restaurant_id",
            table: "backtest_cohort",
            column: "restaurant_id");

        migrationBuilder.CreateIndex(
            name: "idx_backtest_cohort_cohort_type",
            table: "backtest_cohort",
            column: "cohort_type");

        migrationBuilder.CreateIndex(
            name: "idx_backtest_cohort_baseline_date",
            table: "backtest_cohort",
            column: "baseline_date");

        migrationBuilder.CreateIndex(
            name: "idx_backtest_cohort_risk_band",
            table: "backtest_cohort",
            column: "baseline_risk_band");
    }

    /// <inheritdoc />
    protected override void Down(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.DropTable(name: "backtest_cohort");
        migrationBuilder.DropTable(name: "closed_restaurants");
    }
}
