using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace BizHealthApi.Migrations;

public partial class Phase14BusinessStatusHours : Migration
{
    protected override void Up(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.AddColumn<string>(
            name: "business_status",
            table: "health_scores",
            type: "varchar",
            nullable: true);

        migrationBuilder.AddColumn<bool>(
            name: "temporarily_closed",
            table: "health_scores",
            type: "boolean",
            nullable: true);

        migrationBuilder.AddColumn<bool>(
            name: "permanently_closed",
            table: "health_scores",
            type: "boolean",
            nullable: true);

        migrationBuilder.AddColumn<decimal>(
            name: "total_weekly_hours",
            table: "health_scores",
            type: "decimal",
            nullable: true);

        migrationBuilder.AddColumn<decimal>(
            name: "hours_reduction_pct",
            table: "health_scores",
            type: "decimal",
            nullable: true);

        migrationBuilder.AddColumn<bool>(
            name: "hours_reduction",
            table: "health_scores",
            type: "boolean",
            nullable: true);
    }

    protected override void Down(MigrationBuilder migrationBuilder)
    {
        migrationBuilder.DropColumn(name: "business_status",     table: "health_scores");
        migrationBuilder.DropColumn(name: "temporarily_closed",  table: "health_scores");
        migrationBuilder.DropColumn(name: "permanently_closed",  table: "health_scores");
        migrationBuilder.DropColumn(name: "total_weekly_hours",  table: "health_scores");
        migrationBuilder.DropColumn(name: "hours_reduction_pct", table: "health_scores");
        migrationBuilder.DropColumn(name: "hours_reduction",     table: "health_scores");
    }
}
